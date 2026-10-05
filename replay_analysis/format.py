"""带版本的回放产物帧格式解析与写入。

二进制布局（小端）：
    头部:  magic(4B)="RPLY" | format_version(u16) | min_reader_version(u16)
    段帧:  payload_len(u32) | crc32(payload)(u32) | payload(JSON UTF-8)

段 payload JSON 结构：
    {"segment_id": str,
     "rounds": [{"round_id": str, "revision": int, "tick": int,
                 "events": [...]}, ...]}

版本策略：
    - SUPPORTED_VERSION 是本实现能写的最高版本；
    - 读取时要求 1 <= format_version <= SUPPORTED_VERSION
      且 min_reader_version <= SUPPORTED_VERSION，否则抛 UnsupportedVersionError；
    - v1 的 round 允许缺省 "revision" 字段，读取时按 1 补齐（声明兼容的旧版本可继续分析）。
"""

from __future__ import annotations

import json
import struct
import zlib
from dataclasses import dataclass, field
from typing import Any, BinaryIO, Iterator

from .errors import (
    CorruptDataError,
    SegmentTooLargeError,
    TruncatedDataError,
    UnsupportedVersionError,
)

MAGIC = b"RPLY"
SUPPORTED_VERSION = 2
OLDEST_READABLE_VERSION = 1

_HEADER = struct.Struct("<4sHH")
_FRAME = struct.Struct("<II")

DEFAULT_MAX_SEGMENT_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class RoundRecord:
    """一回合的一次分析结果。revision 单调递增表示更正。"""

    round_id: str
    revision: int
    tick: int
    events: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class Segment:
    """一段回放产物，可乱序/重复到达，按 segment_id 去重。"""

    segment_id: str
    rounds: tuple[RoundRecord, ...] = field(default_factory=tuple)


def _read_exact(stream: BinaryIO, n: int, what: str) -> bytes:
    buf = stream.read(n)
    if buf is None:
        buf = b""
    if len(buf) < n:
        raise TruncatedDataError(
            f"{what}: expected {n} bytes, got {len(buf)}"
        )
    return buf


def read_header(stream: BinaryIO) -> tuple[int, int]:
    """读取并校验头部，返回 (format_version, min_reader_version)。"""
    raw = _read_exact(stream, _HEADER.size, "header")
    magic, version, min_reader = _HEADER.unpack(raw)
    if magic != MAGIC:
        raise CorruptDataError(f"bad magic: {magic!r}")
    if not (OLDEST_READABLE_VERSION <= version <= SUPPORTED_VERSION) or (
        min_reader > SUPPORTED_VERSION
    ):
        raise UnsupportedVersionError(version, min_reader, SUPPORTED_VERSION)
    return version, min_reader


def _parse_round(obj: Any, version: int, segment_id: str) -> RoundRecord:
    if not isinstance(obj, dict):
        raise CorruptDataError(f"segment {segment_id}: round is not an object")
    round_id = obj.get("round_id")
    if not isinstance(round_id, str) or not round_id:
        raise CorruptDataError(f"segment {segment_id}: missing round_id")
    # v1 兼容：旧版本没有 revision 字段，按 1 处理。
    revision = obj.get("revision", 1 if version == 1 else None)
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise CorruptDataError(
            f"segment {segment_id} round {round_id}: bad revision {revision!r}"
        )
    tick = obj.get("tick", 0)
    if not isinstance(tick, int) or isinstance(tick, bool):
        raise CorruptDataError(
            f"segment {segment_id} round {round_id}: bad tick {tick!r}"
        )
    events = obj.get("events", [])
    if not isinstance(events, list) or not all(isinstance(e, dict) for e in events):
        raise CorruptDataError(
            f"segment {segment_id} round {round_id}: events must be objects"
        )
    return RoundRecord(
        round_id=round_id,
        revision=revision,
        tick=tick,
        events=tuple(events),
    )


def parse_segment_payload(payload: bytes, version: int) -> Segment:
    try:
        obj = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CorruptDataError(f"segment payload is not valid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise CorruptDataError("segment payload is not a JSON object")
    segment_id = obj.get("segment_id")
    if not isinstance(segment_id, str) or not segment_id:
        raise CorruptDataError("segment missing segment_id")
    rounds_raw = obj.get("rounds", [])
    if not isinstance(rounds_raw, list):
        raise CorruptDataError(f"segment {segment_id}: rounds is not a list")
    rounds = tuple(_parse_round(r, version, segment_id) for r in rounds_raw)
    return Segment(segment_id=segment_id, rounds=rounds)


def iter_segments(
    stream: BinaryIO,
    *,
    max_segment_bytes: int = DEFAULT_MAX_SEGMENT_BYTES,
) -> Iterator[Segment]:
    """流式逐段解析，不把整个回放载入内存。

    在帧边界遇到 EOF 视为正常结束；帧读到一半 EOF 抛 TruncatedDataError。
    """
    version, _ = read_header(stream)
    while True:
        head = stream.read(_FRAME.size)
        if not head:
            return  # 干净的帧边界 EOF
        if len(head) < _FRAME.size:
            raise TruncatedDataError(
                f"frame header: expected {_FRAME.size} bytes, got {len(head)}"
            )
        (length, crc) = _FRAME.unpack(head)
        if length > max_segment_bytes:
            raise SegmentTooLargeError(length, max_segment_bytes)
        payload = _read_exact(stream, length, "segment payload")
        if zlib.crc32(payload) != crc:
            raise CorruptDataError("segment payload CRC mismatch")
        yield parse_segment_payload(payload, version)


# ---- 写入侧（供测试与本地文件模拟使用） ----


def write_header(
    stream: BinaryIO,
    *,
    version: int = SUPPORTED_VERSION,
    min_reader_version: int = OLDEST_READABLE_VERSION,
) -> None:
    stream.write(_HEADER.pack(MAGIC, version, min_reader_version))


def encode_segment(segment: Segment) -> bytes:
    payload = json.dumps(
        {
            "segment_id": segment.segment_id,
            "rounds": [
                {
                    "round_id": r.round_id,
                    "revision": r.revision,
                    "tick": r.tick,
                    "events": list(r.events),
                }
                for r in segment.rounds
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _FRAME.pack(len(payload), zlib.crc32(payload)) + payload


def write_segment(stream: BinaryIO, segment: Segment) -> None:
    stream.write(encode_segment(segment))
