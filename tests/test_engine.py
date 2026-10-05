import io
import threading
import tracemalloc

from replay_analysis import ReplayEngine, RoundRecord, Segment
from conftest import build_artifact, kill_round


def paired_round(round_id, revision, n):
    """kill 数与 skill 数恒等的回合，用于校验快照内部一致性。"""
    events = tuple([{"type": "kill"}] * n + [{"type": "skill", "skill": "s"}] * n)
    return RoundRecord(round_id, revision, 0, events)


class TestStreamingLoad:
    def test_load_from_file(self, tmp_path):
        data = build_artifact(
            [Segment(f"s{i}", (kill_round(f"r{i}", 1, 1),)) for i in range(100)]
        )
        path = tmp_path / "replay.rply"
        path.write_bytes(data)
        eng = ReplayEngine()
        assert eng.load_file(path) == 100
        assert eng.snapshot().query("kill_count") == {"kills": 100}
        eng.close()

    def test_reader_is_lazy(self):
        """生成器不迭代就不读取流。"""
        from replay_analysis.format import iter_segments

        class CountingStream(io.BytesIO):
            reads = 0

            def read(self, n=-1):
                self.reads += 1
                return super().read(n)

        stream = CountingStream(
            build_artifact([Segment("s1", (kill_round("r1"),))])
        )
        gen = iter_segments(stream)
        assert stream.reads == 0  # 未迭代前不得读取
        seg = next(gen)
        assert stream.reads > 0
        assert seg.segment_id == "s1"
        with __import__("pytest").raises(StopIteration):
            next(gen)

    def test_memory_bounded_with_file_backed_store(self, tmp_path):
        """堆内存占用不得随回放长度线性增长。"""
        def peak_for(n_segments, tag):
            segs = [
                Segment(f"{tag}-s{i}", (kill_round(f"{tag}-r{i}", 1, 2),))
                for i in range(n_segments)
            ]
            path = tmp_path / f"{tag}.rply"
            path.write_bytes(build_artifact(segs))
            eng = ReplayEngine(db_path=str(tmp_path / f"{tag}.db"))
            tracemalloc.start()
            eng.load_file(path)
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            eng.close()
            return peak

        small = peak_for(200, "small")
        large = peak_for(4000, "large")  # 20 倍输入
        assert large < small * 4 + 2 * 1024 * 1024

    def test_duplicate_segments_in_stream_counted_once(self):
        seg = Segment("s1", (kill_round("r1", 1, 3),))
        data = build_artifact([seg, seg, seg])
        eng = ReplayEngine()
        assert eng.load_stream(io.BytesIO(data)) == 1
        assert eng.snapshot().query("kill_count") == {"kills": 3}
        eng.close()


class TestConcurrency:
    def test_queries_always_see_consistent_committed_version(self):
        eng = ReplayEngine()
        stop = threading.Event()
        violations = []

        def reader():
            while not stop.is_set():
                snap = eng.snapshot()
                q = snap.query()
                kills = q.get("kill_count.kills", 0)
                casts = q.get("skill_usage.casts", 0)
                if kills != casts:  # 不变量：两者恒等
                    violations.append((snap.version, kills, casts))

        def writer():
            for i in range(400):
                eng.submit_round(paired_round(f"r{i % 40}", i // 40 + 1, 1))

        readers = [threading.Thread(target=reader) for _ in range(4)]
        for t in readers:
            t.start()
        writer()
        stop.set()
        for t in readers:
            t.join()
        assert violations == []
        # 40 个回合，每回合最终 revision 的贡献为 1 杀
        assert eng.snapshot().query("kill_count") == {"kills": 40}

    def test_slow_query_does_not_block_analysis(self):
        eng = ReplayEngine()
        eng.submit_round(paired_round("r0", 1, 1))
        old = eng.snapshot()  # 慢查询持有多久都不影响写入
        for i in range(1, 200):
            eng.submit_round(paired_round(f"r{i}", 1, 1))
        assert old.version < eng.version
        assert old.query("kill_count") == {"kills": 1}  # 旧快照内容不变
        assert eng.snapshot().query("kill_count") == {"kills": 200}
        eng.close()

    def test_concurrent_correction_and_analysis(self):
        eng = ReplayEngine()
        errors = []

        def analyze():
            for i in range(200):
                eng.submit_segment(
                    Segment(f"seg-{i}", (paired_round(f"r{i}", 1, 1),))
                )

        def correct():
            for i in range(200):
                eng.submit_round(paired_round(f"r{i}", 2, 2))

        threads = [threading.Thread(target=analyze),
                   threading.Thread(target=correct)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 每回合最终都是 revision=2、贡献为 2
        assert eng.snapshot().query("kill_count") == {"kills": 400}
        eng.close()
