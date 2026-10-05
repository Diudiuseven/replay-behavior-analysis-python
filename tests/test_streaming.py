"""流式处理与内存有界性。"""
import io
import tracemalloc

from replay_analysis import ReplayAnalyzer, ReplayReader, ReplayWriter, Round, RoundEvent, Segment


class CountingStream(io.BytesIO):
    """记录累计读取量的只读流。"""

    def __init__(self, data: bytes):
        super().__init__(data)
        self.bytes_read = 0

    def read(self, n=-1):
        data = super().read(n)
        self.bytes_read += len(data)
        return data


def build_replay_bytes(n_segments, rounds_per_segment):
    buf = io.BytesIO()
    writer = ReplayWriter(buf)
    for i in range(n_segments):
        rounds = tuple(
            Round(i * rounds_per_segment + j, (RoundEvent("kill"), RoundEvent("move", {"distance": 1.5})))
            for j in range(rounds_per_segment)
        )
        writer.write_segment(Segment("g", i, rounds))
    writer.finish()
    return buf.getvalue()


def test_reader_is_incremental_not_whole_file():
    data = build_replay_bytes(50, 20)
    stream = CountingStream(data)
    reader = ReplayReader(stream)
    it = reader.segments()
    first = next(it)
    assert first.segment_index == 0
    # 产出第一个分段时读取量远小于全量
    assert stream.bytes_read < len(data) // 2


def test_large_replay_memory_bounded(tmp_path):
    small = build_replay_bytes(10, 20)
    large = build_replay_bytes(400, 50)  # 40 倍数据量

    def peak_for(data, work):
        stream = CountingStream(data)
        analyzer = ReplayAnalyzer("g", work_dir=work)
        tracemalloc.start()
        for seg in ReplayReader(stream).segments():
            analyzer.submit_segment(seg)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        analyzer.close()
        return peak

    peak_small = peak_for(small, tmp_path / "s")
    peak_large = peak_for(large, tmp_path / "l")
    # 数据量放大 40 倍，峰值内存增长必须有界（远小于线性）
    assert peak_large < peak_small * 4 + 2 * 1024 * 1024, (peak_small, peak_large)


def test_contribution_store_on_disk(tmp_path):
    analyzer = ReplayAnalyzer("g", work_dir=tmp_path / "w")
    analyzer.submit_segment(Segment("g", 0, (Round(0, (RoundEvent("kill"),)),)))
    analyzer.close()
    assert (tmp_path / "w" / "contributions.db").exists()
