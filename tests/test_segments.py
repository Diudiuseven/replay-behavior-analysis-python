"""乱序、重复、迟到分段的聚合等价性。"""
import random

from replay_analysis import ReplayAnalyzer, Round, RoundEvent, Segment


def make_round(round_id, kills):
    return Round(round_id, tuple(RoundEvent("kill") for _ in range(kills)))


def make_segments(replay_id, n_segments, rounds_per_segment, kills_fn):
    segs = []
    for i in range(n_segments):
        rounds = tuple(
            make_round(i * rounds_per_segment + j, kills_fn(i * rounds_per_segment + j))
            for j in range(rounds_per_segment)
        )
        segs.append(Segment(replay_id, i, rounds))
    return segs


def totals_of(analyzer):
    return analyzer.query().as_dict()


def test_out_of_order_equals_sequential(tmp_path):
    segs = make_segments("g", 8, 5, lambda rid: rid % 4)
    order = list(range(8))
    random.Random(7).shuffle(order)

    a = ReplayAnalyzer("g", work_dir=tmp_path / "a")
    for i in order:
        a.submit_segment(segs[i])
    b = ReplayAnalyzer("g", work_dir=tmp_path / "b")
    for s in segs:
        b.submit_segment(s)
    assert totals_of(a) == totals_of(b)
    a.close(); b.close()


def test_duplicate_and_late_segments_not_double_counted(tmp_path):
    segs = make_segments("g", 5, 4, lambda rid: 1)
    a = ReplayAnalyzer("g", work_dir=tmp_path / "a")
    for s in segs:
        a.submit_segment(s)
    for s in segs:  # 整批重复到达
        a.submit_segment(s)
    a.submit_segment(segs[0])  # 迟到段
    b = ReplayAnalyzer("g", work_dir=tmp_path / "b")
    for s in segs:
        b.submit_segment(s)
    assert totals_of(a) == totals_of(b)
    assert a.query().rounds_applied == 20
    a.close(); b.close()


def test_same_index_different_content_is_correction(tmp_path):
    a = ReplayAnalyzer("g", work_dir=tmp_path / "a")
    a.submit_segment(Segment("g", 0, (make_round(0, 1),)))
    a.submit_segment(Segment("g", 0, (make_round(0, 5),)))  # 同索引不同内容
    assert a.query().feature("combat")["kills"] == 5.0
    assert a.query().rounds_applied == 1
    a.close()


def test_segment_from_other_replay_rejected(tmp_path):
    a = ReplayAnalyzer("g", work_dir=tmp_path / "a")
    try:
        import pytest

        with pytest.raises(ValueError):
            a.submit_segment(Segment("other", 0, ()))
    finally:
        a.close()


def test_streaming_file_into_analyzer_matches_inmemory(tmp_path):
    from replay_analysis import ReplayReader, ReplayWriter

    segs = make_segments("g", 10, 10, lambda rid: rid % 3)
    path = tmp_path / "replay.rpl"
    with path.open("wb") as fh:
        writer = ReplayWriter(fh)
        for s in segs:
            writer.write_segment(s)
        writer.finish()

    streamed = ReplayAnalyzer("g", work_dir=tmp_path / "s")
    with path.open("rb") as fh:
        for seg in ReplayReader(fh).segments():  # 边读边提交，不整份载入
            streamed.submit_segment(seg)
    direct = ReplayAnalyzer("g", work_dir=tmp_path / "d")
    for s in segs:
        direct.submit_segment(s)
    assert totals_of(streamed) == totals_of(direct)
    streamed.close(); direct.close()
