import io
import json
import struct
import zlib

import pytest

from replay_analysis.format import (
    RoundRecord,
    Segment,
    write_header,
    write_segment,
)


def kill_round(round_id, revision=1, kills=1, tick=0):
    return RoundRecord(
        round_id, revision, tick,
        tuple({"type": "kill"} for _ in range(kills)),
    )


def build_artifact(segments, version=2, min_reader_version=1):
    buf = io.BytesIO()
    write_header(buf, version=version, min_reader_version=min_reader_version)
    for seg in segments:
        write_segment(buf, seg)
    return buf.getvalue()


def build_v1_artifact(round_dicts, segment_id="v1-seg"):
    """手工构造 v1 产物：round 无 revision 字段。"""
    payload = json.dumps(
        {"segment_id": segment_id, "rounds": round_dicts}
    ).encode()
    buf = io.BytesIO()
    write_header(buf, version=1, min_reader_version=1)
    buf.write(struct.pack("<II", len(payload), zlib.crc32(payload)))
    buf.write(payload)
    return buf.getvalue()


@pytest.fixture
def make_segments():
    """生成 n 段、每段 1 回合的确定性数据。"""
    def _make(n, kills_fn=None):
        kills_fn = kills_fn or (lambda i: i % 3)
        return [
            Segment(f"seg-{i}", (kill_round(f"round-{i}", 1, kills_fn(i)),))
            for i in range(n)
        ]
    return _make
