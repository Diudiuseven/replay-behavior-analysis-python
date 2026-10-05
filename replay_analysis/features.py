"""行为特征：可注册、互相独立、结果确定。

每个特征是一个纯函数 extractor: Round -> {指标名: 数值}。
- 各特征的贡献在独立命名空间下聚合，新增特征不改变已有特征结果；
- 同一回合的重算必须幂等：extractor 只依赖回合内容本身；
- 输出按键排序，保证相同输入下逐条一致。
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

from replay_analysis.model import Round

Contribution = Mapping[str, float]
Extractor = Callable[[Round], Contribution]


class FeatureRegistry:
    """特征注册表；新增特征不影响已有特征。"""

    def __init__(self) -> None:
        self._extractors: dict[str, Extractor] = {}

    def register(self, name: str, extractor: Extractor) -> None:
        if not name or ":" in name:
            raise ValueError(f"非法特征名: {name!r}")
        if name in self._extractors:
            raise ValueError(f"特征已注册: {name!r}")
        self._extractors[name] = extractor

    def names(self) -> list[str]:
        return sorted(self._extractors)

    def compute(self, round_: Round) -> dict[str, dict[str, float]]:
        """计算一个回合的全部特征贡献（按键排序，结果确定）。"""
        return {
            name: dict(sorted(self._extractors[name](round_).items()))
            for name in self.names()
        }


def _num(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _combat(round_: Round) -> Contribution:
    kills = deaths = assists = 0
    damage = 0.0
    for e in round_.events:
        if e.kind == "kill":
            kills += 1
        elif e.kind == "death":
            deaths += 1
        elif e.kind == "assist":
            assists += 1
        elif e.kind == "damage":
            damage += _num(e.payload.get("amount"))
    return {
        "kills": float(kills),
        "deaths": float(deaths),
        "assists": float(assists),
        "damage_total": damage,
        "damage_events": float(sum(1 for e in round_.events if e.kind == "damage")),
    }


def _economy(round_: Round) -> Contribution:
    earned = spent = 0.0
    for e in round_.events:
        if e.kind == "gold_earned":
            earned += _num(e.payload.get("amount"))
        elif e.kind == "gold_spent":
            spent += _num(e.payload.get("amount"))
    return {"gold_earned": earned, "gold_spent": spent, "gold_net": earned - spent}


def _movement(round_: Round) -> Contribution:
    distance = 0.0
    teleports = 0
    for e in round_.events:
        if e.kind == "move":
            distance += _num(e.payload.get("distance"))
        elif e.kind == "teleport":
            teleports += 1
    return {"distance": distance, "teleports": float(teleports)}


def _activity(round_: Round) -> Contribution:
    kinds: dict[str, float] = {}
    for e in round_.events:
        kinds[e.kind] = kinds.get(e.kind, 0.0) + 1.0
    out: dict[str, float] = {"events_total": float(len(round_.events))}
    out.update({f"kind:{k}": v for k, v in sorted(kinds.items())})
    return out


def default_registry() -> FeatureRegistry:
    """内置特征集合。"""
    registry = FeatureRegistry()
    registry.register("combat", _combat)
    registry.register("economy", _economy)
    registry.register("movement", _movement)
    registry.register("activity", _activity)
    return registry
