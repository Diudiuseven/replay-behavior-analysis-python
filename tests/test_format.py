import io

import pytest

from replay_analysis.errors import (
    CorruptDataError,
    SegmentTooLargeError,
    TruncatedDataError,
    UnsupportedVersionError,
)
from replay_analysis.format import Segment, iter_segments
from conftest import build_artifact, build_v1_artifact, kill_round


def parse_all(data, **kw):
    return list(iter_segments(io.BytesIO(data), **kw))


class TestVersioning:
    def test_current_version_roundtrip(self):
        data = build_artifact([Segment("s1", (kill_round("r1"),))])
        segs = parse_all(data)
        assert segs[0].segment_id == "s1"
        assert segs[0].rounds[0].round_id == "r1"

    def test_v1_compatible_still_analyzable(self):
        data = build_v1_artifact([{"round_id": "r9", "tick": 3, "events": []}])
        segs = parse_all(data)
        assert segs[0].rounds[0].revision == 1  # 缺省 revision 补 1

    def test_newer_version_rejected(self):
        data = build_artifact([], version=3, min_reader_version=1)
        with pytest.raises(UnsupportedVersionError):
            parse_all(data)

    def test_min_reader_too_new_rejected(self):
        data = build_artifact([], version=2, min_reader_version=99)
        with pytest.raises(UnsupportedVersionError):
            parse_all(data)

    def test_version_zero_rejected(self):
        data = build_artifact([], version=0, min_reader_version=0)
        with pytest.raises(UnsupportedVersionError):
            parse_all(data)


class TestCorruption:
    def test_bad_magic(self):
        data = b"XXXX" + build_artifact([])[4:]
        with pytest.raises(CorruptDataError):
            parse_all(data)

    def test_crc_mismatch(self):
        data = bytearray(build_artifact([Segment("s1", (kill_round("r1"),))]))
        data[-1] ^= 0xFF
        with pytest.raises(CorruptDataError):
            parse_all(bytes(data))

    def test_oversized_segment_rejected(self):
        data = build_artifact([Segment("s1", (kill_round("r1"),))])
        with pytest.raises(SegmentTooLargeError):
            parse_all(data, max_segment_bytes=8)

    def test_distinguishable_reasons(self):
        with pytest.raises(UnsupportedVersionError):
            parse_all(build_artifact([], version=9, min_reader_version=9))
        with pytest.raises(CorruptDataError):
            parse_all(b"XXXX" + build_artifact([])[4:])
        with pytest.raises(TruncatedDataError):
            parse_all(build_artifact([Segment("s1", (kill_round("r1"),))])[:-5])
        # 三类错误互不混淆
        assert not issubclass(TruncatedDataError, CorruptDataError)
        assert not issubclass(UnsupportedVersionError, CorruptDataError)


class TestTruncation:
    def test_truncated_header(self):
        with pytest.raises(TruncatedDataError):
            parse_all(build_artifact([])[:4])

    def test_truncated_frame_header(self):
        data = build_artifact([Segment("s1", (kill_round("r1"),))])
        with pytest.raises(TruncatedDataError):
            parse_all(data[: 8 + 3])

    def test_truncated_payload(self):
        data = build_artifact([Segment("s1", (kill_round("r1"),))])
        with pytest.raises(TruncatedDataError):
            parse_all(data[:-2])

    def test_clean_eof_at_boundary_ok(self):
        data = build_artifact([Segment("s1", (kill_round("r1"),))])
        assert len(parse_all(data)) == 1
