"""版本兼容与损坏/截断输入的加载期校验。"""
import io
import json
import struct
import zlib

import pytest

from replay_analysis import (
    CorruptedReplayError,
    ReplayReader,
    ReplayWriter,
    Round,
    RoundEvent,
    Segment,
    TruncatedReplayError,
    UnsupportedVersionError,
)


def make_artifact(version=2, segments=()):
    buf = io.BytesIO()
    writer = ReplayWriter(buf, version=version)
    for seg in segments:
        writer.write_segment(seg)
    writer.finish()
    return buf.getvalue()


def test_roundtrip_current_version():
    data = make_artifact(segments=[Segment("g", 0, (Round(1, (RoundEvent("kill"),)),))])
    reader = ReplayReader(io.BytesIO(data))
    assert reader.version == 2
    segs = list(reader.segments())
    assert segs[0].rounds[0].events[0].kind == "kill"


def test_older_version_accepted_when_compatible():
    data = make_artifact(version=1, segments=[Segment("g", 0, (Round(7, ()),))])
    reader = ReplayReader(io.BytesIO(data))
    assert reader.version == 1
    assert list(reader.segments())[0].rounds[0].round_id == 7


def test_unsupported_newer_version_rejected():
    buf = io.BytesIO()
    buf.write(struct.pack("<4sHH", b"RPLY", 99, 1))
    buf.seek(0)
    with pytest.raises(UnsupportedVersionError):
        ReplayReader(buf)


def test_min_reader_version_newer_than_reader_rejected():
    buf = io.BytesIO()
    buf.write(struct.pack("<4sHH", b"RPLY", 1, 99))
    buf.seek(0)
    with pytest.raises(UnsupportedVersionError):
        ReplayReader(buf)


def test_writer_rejects_unknown_version():
    with pytest.raises(UnsupportedVersionError):
        ReplayWriter(io.BytesIO(), version=77)


def test_bad_magic_is_corruption_not_version_error():
    with pytest.raises(CorruptedReplayError):
        ReplayReader(io.BytesIO(b"NOPE" + b"\x01\x00\x01\x00"))


def test_checksum_mismatch_is_corruption():
    data = bytearray(make_artifact(segments=[Segment("g", 0, (Round(1, ()),))]))
    data[-1] ^= 0xFF  # 翻转结束帧负载的一个字节
    with pytest.raises(CorruptedReplayError):
        list(ReplayReader(io.BytesIO(bytes(data))).segments())


def test_invalid_json_payload_is_corruption():
    payload = b"not-json"
    buf = io.BytesIO()
    buf.write(struct.pack("<4sHH", b"RPLY", 2, 1))
    buf.write(struct.pack("<II", len(payload), zlib.crc32(payload) & 0xFFFFFFFF))
    buf.write(payload)
    buf.seek(0)
    with pytest.raises(CorruptedReplayError):
        list(ReplayReader(buf).segments())


def test_empty_input_is_truncation():
    with pytest.raises(TruncatedReplayError):
        ReplayReader(io.BytesIO(b""))


def test_short_header_is_truncation():
    with pytest.raises(TruncatedReplayError):
        ReplayReader(io.BytesIO(b"RPL"))


def test_missing_end_frame_is_truncation():
    buf = io.BytesIO()
    writer = ReplayWriter(buf)
    writer.write_segment(Segment("g", 0, ()))
    # 不写 end 帧
    with pytest.raises(TruncatedReplayError):
        list(ReplayReader(io.BytesIO(buf.getvalue())).segments())


def test_cut_payload_is_truncation():
    data = make_artifact(segments=[Segment("g", 0, (Round(1, ()),))])
    with pytest.raises(TruncatedReplayError):
        list(ReplayReader(io.BytesIO(data[:-2])).segments())


def test_error_types_are_distinguishable():
    # 三类原因分属三个可区分的异常类型
    assert issubclass(UnsupportedVersionError, Exception)
    assert CorruptedReplayError is not TruncatedReplayError
    assert not issubclass(UnsupportedVersionError, CorruptedReplayError)
    assert not issubclass(TruncatedReplayError, CorruptedReplayError)


def test_v2_extra_fields_ignored_for_forward_compat():
    # v2 回合允许额外字段，读取时忽略
    frame = {
        "type": "segment",
        "replay_id": "g",
        "segment_index": 0,
        "rounds": [{"round_id": 1, "events": [], "timestamp": 12345}],
    }
    payload = json.dumps(frame).encode()
    end = json.dumps({"type": "end"}).encode()
    buf = io.BytesIO()
    buf.write(struct.pack("<4sHH", b"RPLY", 2, 1))
    for p in (payload, end):
        buf.write(struct.pack("<II", len(p), zlib.crc32(p) & 0xFFFFFFFF))
        buf.write(p)
    buf.seek(0)
    segs = list(ReplayReader(buf).segments())
    assert segs[0].rounds[0].round_id == 1
