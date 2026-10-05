"""带版本的回放产物读写（流式，不整份载入内存）。

二进制布局：
    头部: MAGIC(4B) | format_version(u16) | min_reader_version(u16)
    帧:   payload_length(u32) | crc32(u32) | payload(JSON bytes)
    最后一帧为 {"type": "end"}，其余为 {"type": "segment", ...}。

版本策略：
    - 读取器支持 SUPPORTED_VERSIONS 中的版本；
    - 产物声明 min_reader_version，读取器版本低于它时拒绝；
    - 版本不被支持 -> UnsupportedVersionError
    - 魔数/校验和/JSON 非法 -> CorruptedReplayError
    - 长度前缀声明的字节未完整到达 -> TruncatedReplayError
"""
from __future__ import annotations

import json
import struct
import zlib
from typing import Any, BinaryIO, Iterator

from replay_analysis.errors import (
    CorruptedReplayError,
    TruncatedReplayError,
    UnsupportedVersionError,
)
from replay_analysis.model import Round, RoundEvent, Segment

MAGIC = b"RPLY"
FORMAT_VERSION = 2
MIN_SUPPORTED_VERSION = 1
SUPPORTED_VERSIONS = (1, 2)

_HEADER = struct.Struct("<4sHH")
_FRAME_PREFIX = struct.Struct("<II")


def _round_to_dict(round_: Round) -> dict[str, Any]:
    return {
        "round_id": round_.round_id,
        "events": [{"kind": e.kind, "payload": e.payload} for e in round_.events],
    }


def _round_from_dict(data: Any, version: int) -> Round:
    if not isinstance(data, dict) or not isinstance(data.get("round_id"), int):
        raise CorruptedReplayError(f"非法回合记录: {data!r}")
    raw_events = data.get("events", [])
    if not isinstance(raw_events, list):
        raise CorruptedReplayError(f"非法事件列表: {raw_events!r}")
    events = []
    for raw in raw_events:
        if not isinstance(raw, dict) or not isinstance(raw.get("kind"), str):
            raise CorruptedReplayError(f"非法事件记录: {raw!r}")
        payload = raw.get("payload", {})
        if not isinstance(payload, dict):
            raise CorruptedReplayError(f"非法事件负载: {payload!r}")
        events.append(RoundEvent(kind=raw["kind"], payload=payload))
    # v2 允许额外字段（如 "timestamp"），读取时忽略，保证向后兼容。
    return Round(round_id=data["round_id"], events=tuple(events))


class ReplayWriter:
    """把分段流式写入二进制产物。"""

    def __init__(self, sink: BinaryIO, version: int = FORMAT_VERSION) -> None:
        if version not in SUPPORTED_VERSIONS:
            raise UnsupportedVersionError(f"无法写入不支持的版本: {version}")
        self._sink = sink
        self._version = version
        sink.write(_HEADER.pack(MAGIC, version, MIN_SUPPORTED_VERSION))

    def _write_frame(self, obj: dict[str, Any]) -> None:
        payload = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
        crc = zlib.crc32(payload) & 0xFFFFFFFF
        self._sink.write(_FRAME_PREFIX.pack(len(payload), crc))
        self._sink.write(payload)

    def write_segment(self, segment: Segment) -> None:
        self._write_frame(
            {
                "type": "segment",
                "replay_id": segment.replay_id,
                "segment_index": segment.segment_index,
                "rounds": [_round_to_dict(r) for r in segment.rounds],
            }
        )

    def finish(self) -> None:
        self._write_frame({"type": "end"})


class ReplayReader:
    """流式读取回放产物，逐个产出分段。

    构造时只读取并校验头部；分段在迭代时逐帧解析，
    内存占用与回放总长度无关。
    """

    def __init__(self, source: BinaryIO) -> None:
        self._source = source
        header = source.read(_HEADER.size)
        if len(header) == 0:
            raise TruncatedReplayError("空输入：缺少文件头")
        if len(header) < _HEADER.size:
            raise TruncatedReplayError("文件头被截断")
        magic, version, min_reader = _HEADER.unpack(header)
        if magic != MAGIC:
            raise CorruptedReplayError(f"魔数不匹配: {magic!r}")
        if version not in SUPPORTED_VERSIONS or min_reader > FORMAT_VERSION:
            raise UnsupportedVersionError(
                f"产物版本={version}, 要求读取器>={min_reader}, "
                f"当前读取器版本={FORMAT_VERSION}"
            )
        self._version = version

    @property
    def version(self) -> int:
        return self._version

    def _read_exact(self, n: int) -> bytes:
        data = self._source.read(n)
        if len(data) < n:
            raise TruncatedReplayError(f"期望 {n} 字节，实际 {len(data)} 字节")
        return data

    def _read_frame(self) -> dict[str, Any] | None:
        prefix = self._source.read(_FRAME_PREFIX.size)
        if len(prefix) == 0:
            raise TruncatedReplayError("缺少结束帧：产物在分段中途结束")
        if len(prefix) < _FRAME_PREFIX.size:
            raise TruncatedReplayError("帧前缀被截断")
        length, crc = _FRAME_PREFIX.unpack(prefix)
        payload = self._read_exact(length)
        if (zlib.crc32(payload) & 0xFFFFFFFF) != crc:
            raise CorruptedReplayError("帧校验和不匹配")
        try:
            obj = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise CorruptedReplayError(f"帧负载不是合法 JSON: {exc}") from exc
        if not isinstance(obj, dict) or "type" not in obj:
            raise CorruptedReplayError(f"帧负载缺少类型字段: {obj!r}")
        return obj

    def segments(self) -> Iterator[Segment]:
        while True:
            frame = self._read_frame()
            assert frame is not None
            if frame["type"] == "end":
                return
            if frame["type"] != "segment":
                raise CorruptedReplayError(f"未知帧类型: {frame['type']!r}")
            rounds_raw = frame.get("rounds")
            if (
                not isinstance(frame.get("replay_id"), str)
                or not isinstance(frame.get("segment_index"), int)
                or not isinstance(rounds_raw, list)
            ):
                raise CorruptedReplayError(f"分段帧字段非法: {frame!r}")
            rounds = tuple(_round_from_dict(r, self._version) for r in rounds_raw)
            yield Segment(
                replay_id=frame["replay_id"],
                segment_index=frame["segment_index"],
                rounds=rounds,
            )
