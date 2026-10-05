"""特征自洽性、确定性与可扩展性。"""
from replay_analysis import FeatureRegistry, ReplayAnalyzer, Round, RoundEvent, Segment, default_registry


def sample_rounds():
    return [
        Round(0, (RoundEvent("kill"), RoundEvent("damage", {"amount": 100}), RoundEvent("move", {"distance": 10.0}))),
        Round(1, (RoundEvent("death"), RoundEvent("gold_earned", {"amount": 250}), RoundEvent("teleport"))),
        Round(2, (RoundEvent("kill"), RoundEvent("kill"), RoundEvent("gold_spent", {"amount": 50}))),
    ]


def analyze(tmp_path, name, registry, rounds):
    a = ReplayAnalyzer("g", work_dir=tmp_path / name, registry=registry)
    a.submit_segment(Segment("g", 0, tuple(rounds)))
    snap = a.query().as_dict()
    a.close()
    return snap


def test_same_input_same_order_identical_output(tmp_path):
    rounds = sample_rounds()
    r1 = analyze(tmp_path, "a", default_registry(), rounds)
    r2 = analyze(tmp_path, "b", default_registry(), rounds)
    assert r1 == r2
    # 输出键有序，逐条一致
    for metrics in r1.values():
        assert list(metrics) == sorted(metrics)


def test_new_feature_does_not_change_existing(tmp_path):
    rounds = sample_rounds()
    base = analyze(tmp_path, "a", default_registry(), rounds)

    extended = default_registry()
    extended.register("objective", lambda r: {"towers": sum(1.0 for e in r.events if e.kind == "tower")})
    grown = analyze(tmp_path, "b", extended, rounds)

    assert set(grown) == set(base) | {"objective"}
    for name in base:
        assert grown[name] == base[name]


def test_features_self_consistent_on_same_input(tmp_path):
    rounds = sample_rounds()
    snap = analyze(tmp_path, "a", default_registry(), rounds)
    # combat 与 activity 对同一输入的击杀计数必须一致
    assert snap["combat"]["kills"] == snap["activity"]["kind:kill"]
    assert snap["combat"]["damage_events"] == snap["activity"]["kind:damage"]
    # economy 内部自洽
    assert snap["economy"]["gold_net"] == snap["economy"]["gold_earned"] - snap["economy"]["gold_spent"]


def test_duplicate_feature_name_rejected():
    reg = FeatureRegistry()
    reg.register("x", lambda r: {})
    import pytest

    with pytest.raises(ValueError):
        reg.register("x", lambda r: {})
