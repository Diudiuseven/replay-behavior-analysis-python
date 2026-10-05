"""增量聚合器：段去重、按回合更正、可复现的聚合版本。

聚合语义：
    - 段按 segment_id 去重，重复/迟到段直接忽略；
    - 每回合保留最高 revision 的贡献，totals = Σ 各回合最新贡献；
    - 更正（更高 revision）= 减去旧贡献 + 加上新贡献，整数定点
      运算保证更正结果与从头重算逐项一致；
    - 同 revision 重复提交为幂等空操作；同 revision 数据冲突时
      保留先到者（确定性策略，见 README）。

内存边界：
    每回合贡献与段去重集合存放在 sqlite（默认 :memory:，大体量
    场景传文件路径即可落盘），进程堆内只保留 O(特征数×指标数)
    的运行合计，不随回放长度增长。

并发：
    所有变更在内部锁下串行提交，每次提交产生单调递增的版本号与
    不可变 Snapshot；查询只拿快照引用，慢查询不阻塞分析推进。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from types import MappingProxyType
from typing import Iterable, Mapping

from .features import Feature
from .format import RoundRecord, Segment

_SCHEMA = """
CREATE TABLE IF NOT EXISTS segments (
    segment_id TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS rounds (
    round_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    contribution TEXT NOT NULL
);
"""


class Snapshot:
    """某一已提交版本的不可变聚合结果。"""

    __slots__ = ("version", "_totals")

    def __init__(self, version: int, totals: Mapping[str, int]):
        self.version = version
        self._totals = MappingProxyType(dict(totals))

    @property
    def totals(self) -> Mapping[str, int]:
        """全部指标，键为 ``feature.metric``，按键排序。"""
        return self._totals

    def query(self, feature: str | None = None) -> dict[str, int]:
        """查询单个特征或全部特征的指标。"""
        if feature is None:
            return dict(self._totals)
        prefix = feature + "."
        return {
            k[len(prefix):]: v
            for k, v in self._totals.items()
            if k.startswith(prefix)
        }

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, Snapshot)
            and self.version == other.version
            and dict(self._totals) == dict(other._totals)
        )

    def __repr__(self) -> str:
        return f"Snapshot(version={self.version}, metrics={len(self._totals)})"


class Aggregator:
    """线程安全的增量聚合器。"""

    def __init__(self, features: Iterable[Feature], db_path: str = ":memory:"):
        self._features = list(features)
        names = [f.name for f in self._features]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate feature names: {names}")
        self._lock = threading.Lock()
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        # 该库只是派生缓存（回放产物才是事实源），崩溃后重放即可恢复，
        # 因此关闭同步刷盘以避免每段一次 fsync。
        self._db.execute("PRAGMA synchronous = OFF")
        self._db.executescript(_SCHEMA)
        self._totals: dict[str, int] = {}
        self._version = 0
        self._snapshot = Snapshot(0, {})

    # ---- 内部 ----

    def _extract(self, round_record: RoundRecord) -> dict[str, int]:
        contribution: dict[str, int] = {}
        for feature in self._features:
            for key, value in feature.validate(
                feature.extract(round_record)
            ).items():
                contribution[f"{feature.name}.{key}"] = value
        return contribution

    def _apply_round_locked(self, round_record: RoundRecord) -> str:
        row = self._db.execute(
            "SELECT revision, contribution FROM rounds WHERE round_id = ?",
            (round_record.round_id,),
        ).fetchone()
        if row is not None and round_record.revision <= row[0]:
            # 幂等：同 revision 重算或过期更正都不改变结果
            return "duplicate" if round_record.revision == row[0] else "stale"
        new_contrib = self._extract(round_record)
        if row is not None:  # 更正：先减旧贡献
            old_contrib = json.loads(row[1])
            for key, value in old_contrib.items():
                remaining = self._totals.get(key, 0) - value
                if remaining:
                    self._totals[key] = remaining
                else:
                    self._totals.pop(key, None)  # 不留旧值残留
        for key, value in new_contrib.items():
            if value:
                self._totals[key] = self._totals.get(key, 0) + value
        self._db.execute(
            "INSERT OR REPLACE INTO rounds(round_id, revision, contribution)"
            " VALUES (?, ?, ?)",
            (
                round_record.round_id,
                round_record.revision,
                json.dumps(new_contrib, sort_keys=True),
            ),
        )
        return "applied"

    def _commit_locked(self) -> None:
        self._db.commit()
        self._version += 1
        self._snapshot = Snapshot(
            self._version, dict(sorted(self._totals.items()))
        )

    # ---- 变更接口 ----

    def submit_segment(self, segment: Segment) -> bool:
        """提交一个段；重复段返回 False 且不计入。"""
        with self._lock:
            cur = self._db.execute(
                "INSERT OR IGNORE INTO segments(segment_id) VALUES (?)",
                (segment.segment_id,),
            )
            if cur.rowcount == 0:
                return False  # 重复/迟到段，忽略
            for round_record in segment.rounds:
                self._apply_round_locked(round_record)
            self._commit_locked()
            return True

    def submit_round(self, round_record: RoundRecord) -> str:
        """提交单回合分析/更正，返回 applied|duplicate|stale。"""
        with self._lock:
            result = self._apply_round_locked(round_record)
            if result == "applied":
                self._commit_locked()
            else:
                self._db.commit()
            return result

    # ---- 查询接口 ----

    def snapshot(self) -> Snapshot:
        """获取当前已提交的完整聚合版本（不阻塞变更）。"""
        with self._lock:
            return self._snapshot

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    def close(self) -> None:
        with self._lock:
            self._db.close()
