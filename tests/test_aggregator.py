import random

from replay_analysis import Aggregator, Feature, RoundRecord, Segment, default_features
from conftest import kill_round, make_segments  # noqa: F401  (fixture module)


def aggregate(segments):
    agg = Aggregator(default_features())
    for seg in segments:
        agg.submit_segment(seg)
    return agg


class TestOutOfOrderAndDuplicates:
    def test_shuffled_equals_sequential(self, make_segments):
        segs = make_segments(50)
        expected = aggregate(segs).snapshot().totals
        rng = random.Random(42)
        for _ in range(5):
            shuffled = segs[:]
            rng.shuffle(shuffled)
            assert aggregate(shuffled).snapshot().totals == expected

    def test_duplicate_segments_not_counted(self, make_segments):
        segs = make_segments(20)
        agg = Aggregator(default_features())
        for seg in segs:
            assert agg.submit_segment(seg)
        for seg in segs:  # 全部重复到达
            assert not agg.submit_segment(seg)
        assert agg.snapshot().totals == aggregate(segs).snapshot().totals

    def test_late_duplicate_after_correction_ignored(self):
        agg = Aggregator(default_features())
        seg = Segment("s1", (kill_round("r1", 1, 2),))
        agg.submit_segment(seg)
        agg.submit_round(kill_round("r1", 2, 7))  # 更正
        assert not agg.submit_segment(seg)  # 迟到的重复段不得覆盖更正
        assert agg.snapshot().query("kill_count") == {"kills": 7}


class TestCorrection:
    def test_correction_equals_fresh_recompute(self, make_segments):
        segs = make_segments(30)
        agg = aggregate(segs)
        # 对若干回合做更正
        corrections = {
            "round-3": kill_round("round-3", 2, 9),
            "round-17": kill_round("round-17", 3, 1),
        }
        for rec in corrections.values():
            assert agg.submit_round(rec) == "applied"
        # 从头重算（直接以更正后的数据喂入新聚合器）
        fresh = Aggregator(default_features())
        for i, seg in enumerate(segs):
            rec = corrections.get(f"round-{i}", seg.rounds[0])
            fresh.submit_segment(Segment(seg.segment_id, (rec,)))
        assert agg.snapshot().totals == fresh.snapshot().totals

    def test_correction_removes_old_values(self):
        agg = Aggregator(default_features())
        agg.submit_round(kill_round("r1", 1, 5))
        agg.submit_round(kill_round("r1", 2, 0))  # 更正为 0
        assert agg.snapshot().query("kill_count") == {}  # 无残留旧值

    def test_reanalysis_idempotent(self):
        agg = Aggregator(default_features())
        rec = kill_round("r1", 1, 3)
        assert agg.submit_round(rec) == "applied"
        snap = agg.snapshot()
        assert agg.submit_round(rec) == "duplicate"
        assert agg.submit_round(rec) == "duplicate"
        assert agg.snapshot().totals == snap.totals
        assert agg.snapshot().version == snap.version

    def test_stale_revision_ignored(self):
        agg = Aggregator(default_features())
        agg.submit_round(kill_round("r1", 3, 4))
        assert agg.submit_round(kill_round("r1", 2, 99)) == "stale"
        assert agg.snapshot().query("kill_count") == {"kills": 4}

    def test_multiple_corrections_accumulate_once(self):
        agg = Aggregator(default_features())
        for rev in range(1, 6):
            agg.submit_round(kill_round("r1", rev, rev))
        assert agg.snapshot().query("kill_count") == {"kills": 5}


class DeterministicFeature(Feature):
    def __init__(self, name, metrics):
        self.name = name
        self._metrics = metrics

    def extract(self, round_record):
        return dict(self._metrics)


class TestFeatureIsolation:
    def test_new_feature_does_not_change_existing(self, make_segments):
        segs = make_segments(20)
        base = Aggregator(default_features())
        for s in segs:
            base.submit_segment(s)
        extended = Aggregator(
            default_features()
            + [DeterministicFeature("extra", {"score": 2})]
        )
        for s in segs:
            extended.submit_segment(s)
        for name in ("kill_count", "total_damage", "skill_usage",
                     "movement_distance"):
            assert base.snapshot().query(name) == extended.snapshot().query(name)
        assert extended.snapshot().query("extra") == {"score": 40}

    def test_duplicate_feature_names_rejected(self):
        import pytest

        with pytest.raises(ValueError):
            Aggregator([DeterministicFeature("x", {}),
                        DeterministicFeature("x", {})])

    def test_same_input_same_output_across_runs(self, make_segments):
        segs = make_segments(40)
        results = {tuple(aggregate(segs).snapshot().totals.items()) for _ in range(3)}
        assert len(results) == 1
