"""面向使用者的回放分析器：分段摄入、去重、更正、并发查询。

- 分段可乱序、可重复到达：按 (segment_index, 内容哈希) 去重，
  重复段与迟到段不会被重复计入；
- 同一分段索引若携带不同内容，视为对所含回合的更正重新分析；
- 查询返回已提交的不可变快照，慢查询只持有引用，不阻塞分析推进。
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import threading
from pathlib import Path

from replay_analysis.aggregator import Aggregator, Snapshot
from replay_analysis.features import FeatureRegistry, default_registry
from replay_analysis.model import Round, Segment
from replay_analysis.store import ContributionStore, SqliteContributionStore


def _segment_fingerprint(segment: Segment) -> str:
    payload = json.dumps(
        {
            "segment_index": segment.segment_index,
            "rounds": [
                {
                    "round_id": r.round_id,
                    "events": [{"kind": e.kind, "payload": e.payload} for e in r.events],
                }
                for r in segment.rounds
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ReplayAnalyzer:
    """线程安全；查询读取已提交快照，不阻塞分析推进。"""

    def __init__(
        self,
        replay_id: str,
        work_dir: str | Path | None = None,
        registry: FeatureRegistry | None = None,
    ) -> None:
        self._replay_id = replay_id
        self._registry = registry or default_registry()
        self._owns_dir = work_dir is None
        self._work_dir = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="replay-analysis-"))
        self._work_dir.mkdir(parents=True, exist_ok=True)
        # 落盘贡献存储：内存占用不随回放长度增长。
        self._store: ContributionStore = SqliteContributionStore(self._work_dir / "contributions.db")
        self._aggregator = Aggregator(self._registry, self._store)
        self._segments_seen: dict[int, str] = {}
        self._lock = threading.Lock()

    def _check_replay(self, segment: Segment) -> None:
        if segment.replay_id != self._replay_id:
            raise ValueError(
                f"分段属于回放 {segment.replay_id!r}，当前分析器处理 {self._replay_id!r}"
            )

    def submit_segment(self, segment: Segment) -> int:
        """摄入分段（可乱序/重复）；返回已提交版本号。"""
        self._check_replay(segment)
        fingerprint = _segment_fingerprint(segment)
        with self._lock:
            if self._segments_seen.get(segment.segment_index) == fingerprint:
                return self._aggregator.latest().version  # 重复段：直接忽略
            self._segments_seen[segment.segment_index] = fingerprint
            for round_ in segment.rounds:
                self._aggregator.apply_round(round_)
            return self._aggregator.commit().version

    def correct_round(self, round_: Round) -> int:
        """更正单个回合；结果与从头重算逐项一致。"""
        with self._lock:
            self._aggregator.apply_round(round_)
            return self._aggregator.commit().version

    def query(self) -> Snapshot:
        """获取当前最新已提交快照。"""
        return self._aggregator.latest()

    def close(self) -> None:
        with self._lock:
            self._store.close()

    def __enter__(self) -> "ReplayAnalyzer":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
