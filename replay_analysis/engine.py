"""回放分析引擎：流式加载、更正与快照查询的统一入口。

引擎逐段从流中读取回放产物并提交给聚合器，单次分析不会把整份
回放载入内存；查询通过不可变快照进行，与分析/更正并发安全。
"""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO, Iterable

from .aggregator import Aggregator, Snapshot
from .features import Feature, default_features
from .format import (
    DEFAULT_MAX_SEGMENT_BYTES,
    RoundRecord,
    Segment,
    iter_segments,
)


class ReplayEngine:
    """单进程回放分析引擎。"""

    def __init__(
        self,
        features: Iterable[Feature] | None = None,
        *,
        db_path: str = ":memory:",
        max_segment_bytes: int = DEFAULT_MAX_SEGMENT_BYTES,
    ):
        self._aggregator = Aggregator(
            list(features) if features is not None else default_features(),
            db_path=db_path,
        )
        self._max_segment_bytes = max_segment_bytes

    # ---- 加载 ----

    def load_stream(self, stream: BinaryIO) -> int:
        """流式加载一份回放产物，返回实际计入的段数（重复段不计）。"""
        applied = 0
        for segment in iter_segments(
            stream, max_segment_bytes=self._max_segment_bytes
        ):
            if self._aggregator.submit_segment(segment):
                applied += 1
        return applied

    def load_file(self, path: str | Path) -> int:
        """从本地文件流式加载。"""
        with open(path, "rb") as fh:
            return self.load_stream(fh)

    def submit_segment(self, segment: Segment) -> bool:
        """直接提交一个段（内存模拟输入）。"""
        return self._aggregator.submit_segment(segment)

    # ---- 更正 ----

    def submit_round(self, round_record: RoundRecord) -> str:
        """提交单回合分析/更正，返回 applied|duplicate|stale。"""
        return self._aggregator.submit_round(round_record)

    # ---- 查询 ----

    def snapshot(self) -> Snapshot:
        """当前已提交的完整聚合版本。"""
        return self._aggregator.snapshot()

    @property
    def version(self) -> int:
        return self._aggregator.version

    def close(self) -> None:
        self._aggregator.close()

    def __enter__(self) -> "ReplayEngine":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
