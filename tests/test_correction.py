"""重复分析的幂等性与更正后的一致性。"""
import random

from replay_analysis import ReplayAnalyzer, Round, RoundEvent, Segment


def make_round(round_id, kills=0, gold=0):
    events = [RoundEvent("kill") for _ in range(kills)]
    if gold:
        events.append(RoundEvent("gold_earned", {"amount": gold}))
    return Round(round_id, tuple(events))


def fresh_result(tmp_path, name, rounds):
    a = ReplayAnalyzer("g", work_dir=tmp_path / name)
    a.submit_segment(Segment("g", 0, tuple(rounds)))
    result = a.query().as_dict()
    a.close()
    return result


def test_reanalysis_is_idempotent(tmp_path):
    a = ReplayAnalyzer("g", work_dir=tmp_path / "a")
    rounds = [make_round(i, kills=i) for i in range(10)]
    a.submit_segment(Segment("g", 0, tuple(rounds)))
    v1, snap1 = a.query().version, a.query().as_dict()
    # 同一批回合重复分析多次
    for _ in range(3):
        a.submit_segment(Segment("g", 0, tuple(rounds)))
        for r in rounds:
            a.correct_round(r)
    assert a.query().as_dict() == snap1
    assert a.query().version == v1  # 无实际变更则不产生新版本
    a.close()


def test_correction_matches_from_scratch(tmp_path):
    rng = random.Random(1)
    original = [make_round(i, kills=rng.randint(0, 3), gold=rng.randint(0, 200)) for i in range(20)]
    a = ReplayAnalyzer("g", work_dir=tmp_path / "a")
    a.submit_segment(Segment("g", 0, tuple(original)))

    # 随机更正若干回合，有的更正多次
    corrected = list(original)
    for _ in range(30):
        idx = rng.randrange(20)
        corrected[idx] = make_round(idx, kills=rng.randint(0, 5), gold=rng.randint(0, 500))
        a.correct_round(corrected[idx])

    assert a.query().as_dict() == fresh_result(tmp_path, "fresh", corrected)
    a.close()


def test_correction_leaves_no_residuals(tmp_path):
    a = ReplayAnalyzer("g", work_dir=tmp_path / "a")
    a.submit_segment(Segment("g", 0, (make_round(0, kills=4, gold=100),)))
    a.correct_round(make_round(0))  # 更正为空回合：旧值不得残留
    snap = a.query()
    assert snap.feature("combat").get("kills", 0.0) == 0.0
    assert snap.feature("economy").get("gold_earned", 0.0) == 0.0
    assert snap.rounds_applied == 1
    a.close()


def test_correction_via_resent_segment(tmp_path):
    a = ReplayAnalyzer("g", work_dir=tmp_path / "a")
    a.submit_segment(Segment("g", 0, (make_round(0, kills=1), make_round(1, kills=2))))
    a.submit_segment(Segment("g", 1, (make_round(2, kills=3),)))
    # 重发 0 号段，内容更正
    a.submit_segment(Segment("g", 0, (make_round(0, kills=9), make_round(1, kills=2))))
    expected = fresh_result(tmp_path, "f", [make_round(0, kills=9), make_round(1, kills=2), make_round(2, kills=3)])
    assert a.query().as_dict() == expected
    a.close()
