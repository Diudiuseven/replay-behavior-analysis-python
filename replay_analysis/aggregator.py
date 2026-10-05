"""增量聚合器：幂等重算、按回合更正、提交不可变快照。

聚合语义：
- 每个回合的贡献按 round_id 替换式应用：重放同一回合是幂等的，
  提交不同内容即更正（减旧贡献、加新贡献）；
- 合计只依赖"每个 round_id 的最终贡献集合"，与到达顺序无关，
  因此乱序/重复分段与按顺序单次处理结果完全一致；
- commit 产出不可变 Snapshot，查询线程看到的是某个完整已提交版本。
"""
from __future__ import annotations

import threading
from types import MappingProxyType

from replay_analysis.features import FeatureRegistry
from replay_analysis.model import Round
from replay_analysis.store import ContributionStore


def _freeze(totals: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
    return {f: dict(m) for f, m in totals.items()}


class Snapshot:
    """某一已提交版本的完整聚合结果（不可变）。"""

    def __init__(self, version: int, rounds_applied: int, totals: dict[str, dict[str, float]]) -> None:
        self._version = version
        self._rounds_applied = rounds_applied
        self._totals = MappingProxyType({f: MappingProxyType(m) for f, m in _freeze(totals).items()})

    @property
    def version(self) -> int:
        return self._version

    @property
    def rounds_applied(self) -> int:
        return self._rounds_applied

    def feature(self, name: str) -> dict[str, float]:
        return dict(self._totals.get(name, {}))

    def as_dict(self) -> dict[str, dict[str, float]]:
        return {f: dict(sorted(m.items())) for f, m in sorted(self._totals.items())}

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Snapshot) and self.as_dict() == other.as_dict()


class Aggregator:
    """按回合替换式聚合；聚合满足交换律，与到达顺序无关。"""

    def __init__(self, registry: FeatureRegistry, store: ContributionStore) -> None:
        self._registry = registry
        self._store = store
        self._totals: dict[str, dict[str, float]] = {n: {} for n in registry.names()}
        self._rounds_applied = 0
        self._version = 0
        self._dirty = False
        self._lock = threading.Lock()
        self._latest = Snapshot(0, 0, self._totals)

    @staticmethod
    def _add(totals: dict[str, dict[str, float]], contribution: dict[str, dict[str, float]], sign: float) -> None:
        for feature, metrics in contribution.items():
            slot = totals.setdefault(feature, {})
            for metric, value in metrics.items():
                slot[metric] = slot.get(metric, 0.0) + sign * value

    def apply_round(self, round_: Round) -> bool:
        """应用（或更正）一个回合；返回是否产生实际变更。"""
        new = self._registry.compute(round_)
        with self._lock:
            old = self._store.get(round_.round_id)
            if old == new:
                return False  # 幂等：相同内容重复到达不产生变更
            if old is not None:
                self._add(self._totals, old, -1.0)  # 更正：先撤掉旧贡献
            else:
                self._rounds_applied += 1
            self._add(self._totals, new, +1.0)
            self._store.put(round_.round_id, new)
            self._dirty = True
            return True

    def commit(self) -> Snapshot:
        """提交当前状态为新版本；无变更时复用上一版本。"""
        with self._lock:
            if self._dirty:
                self._version += 1
                self._latest = Snapshot(self._version, self._rounds_applied, self._totals)
                self._dirty = False
            return self._latest

    def latest(self) -> Snapshot:
        with self._lock:
            return self._latest
