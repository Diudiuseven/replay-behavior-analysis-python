"""分析、查询、更正并发进行时的快照一致性。"""
import threading
import time

from replay_analysis import ReplayAnalyzer, Round, RoundEvent, Segment


def make_round(round_id, kills):
    return Round(round_id, tuple(RoundEvent("kill") for _ in range(kills)))


def test_queries_observe_committed_snapshots_under_concurrency(tmp_path):
    analyzer = ReplayAnalyzer("g", work_dir=tmp_path / "w")
    stop = threading.Event()
    violations = []

    def producer():
        for i in range(100):
            analyzer.submit_segment(Segment("g", i, (make_round(i, 1),)))

    def corrector():
        for i in range(100):
            analyzer.correct_round(make_round(i, 2))

    def querier():
        while not stop.is_set():
            snap = analyzer.query()
            d = snap.as_dict()
            # 快照必须是某个完整已提交版本：内部不变量跨特征一致
            combat_kills = d.get("combat", {}).get("kills", 0.0)
            activity_kills = d.get("activity", {}).get("kind:kill", 0.0)
            if combat_kills != activity_kills:
                violations.append((snap.version, combat_kills, activity_kills))
            if snap.version < 0 or snap.rounds_applied < 0:
                violations.append(("negative", snap.version))
            time.sleep(0.0005)  # 让渡 GIL，避免空转饿死生产者

    threads = [threading.Thread(target=producer)]
    threads += [threading.Thread(target=corrector) for _ in range(2)]
    threads += [threading.Thread(target=querier) for _ in range(4)]
    for t in threads:
        t.start()
    threads[0].join()
    for t in threads[1:3]:
        t.join()
    stop.set()
    for t in threads[3:]:
        t.join()

    assert not violations
    # 全部更正完成后，最终状态确定
    final = analyzer.query()
    assert final.rounds_applied == 100
    assert final.feature("combat")["kills"] == 200.0
    analyzer.close()


def test_slow_query_does_not_block_analysis(tmp_path):
    analyzer = ReplayAnalyzer("g", work_dir=tmp_path / "w")
    analyzer.submit_segment(Segment("g", 0, (make_round(0, 1),)))

    early_snapshot = analyzer.query()  # 慢查询持有旧快照
    done = threading.Event()

    def producer():
        for i in range(1, 60):
            analyzer.submit_segment(Segment("g", i, (make_round(i, 1),)))
        done.set()

    t = threading.Thread(target=producer)
    start = time.monotonic()
    t.start()
    # 模拟慢查询：持有旧快照的同时做耗时操作
    while not done.is_set():
        _ = early_snapshot.as_dict()
        time.sleep(0.001)
    t.join()
    elapsed = time.monotonic() - start

    assert analyzer.query().rounds_applied == 60
    assert early_snapshot.rounds_applied == 1  # 旧快照内容不被后续分析污染
    assert elapsed < 30  # 分析推进未被慢查询阻塞
    analyzer.close()


def test_snapshot_is_immutable():
    analyzer = ReplayAnalyzer("g")
    analyzer.submit_segment(Segment("g", 0, (make_round(0, 1),)))
    snap = analyzer.query()
    # as_dict 返回副本，调用方修改不影响快照内容
    snap.as_dict()["combat"]["kills"] = 99
    assert snap.feature("combat")["kills"] == 1.0
    assert snap.as_dict()["combat"]["kills"] == 1.0
    analyzer.close()
