"""行为特征定义与可插拔注册。

每个特征把一回合的一次分析结果（RoundRecord）映射为一组
``{指标名: 定点整数值}`` 的贡献。贡献必须是整数（浮点按 scale 缩放为
定点），从而保证加法聚合满足交换律/结合律：乱序、重复去重、更正
重算的结果与按顺序单次处理逐位一致。

新增特征只需新建 Feature 子类并注册，特征之间互不共享状态，
因此新增特征不会改变已有特征的结果。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .format import RoundRecord

DEFAULT_SCALE = 1000  # 浮点定点化默认放大倍数


def to_fixed(value: float, scale: int = DEFAULT_SCALE) -> int:
    """把浮点度量定点化为整数，保证聚合顺序无关。"""
    return int(round(value * scale))


class Feature(ABC):
    """行为特征基类。extract 返回的贡献键在本特征命名空间内唯一。"""

    name: str = "feature"

    @abstractmethod
    def extract(self, round_record: RoundRecord) -> dict[str, int]:
        """从单个回合的一次分析结果中提取指标贡献（整数）。"""

    def validate(self, contribution: dict[str, int]) -> dict[str, int]:
        for key, value in contribution.items():
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(
                    f"feature {self.name}: contribution {key!r} "
                    f"must be int, got {type(value).__name__}"
                )
        return contribution


def _event_fields(events: tuple[dict[str, Any], ...], event_type: str):
    for e in events:
        if e.get("type") == event_type:
            yield e


class KillCountFeature(Feature):
    """击杀事件计数。"""

    name = "kill_count"

    def extract(self, round_record: RoundRecord) -> dict[str, int]:
        kills = sum(1 for _ in _event_fields(round_record.events, "kill"))
        return {"kills": kills}


class TotalDamageFeature(Feature):
    """伤害总量（伤害值本身为整数）。"""

    name = "total_damage"

    def extract(self, round_record: RoundRecord) -> dict[str, int]:
        total = 0
        for e in _event_fields(round_record.events, "damage"):
            amount = e.get("amount", 0)
            if isinstance(amount, int) and not isinstance(amount, bool):
                total += amount
        return {"damage": total}


class SkillUsageFeature(Feature):
    """技能使用次数（总量 + 按技能名细分）。"""

    name = "skill_usage"

    def extract(self, round_record: RoundRecord) -> dict[str, int]:
        out: dict[str, int] = {"casts": 0}
        for e in _event_fields(round_record.events, "skill"):
            out["casts"] += 1
            skill = e.get("skill")
            if isinstance(skill, str) and skill:
                key = f"skill.{skill}"
                out[key] = out.get(key, 0) + 1
        return out


class MovementDistanceFeature(Feature):
    """移动距离（浮点按 scale 定点化）。"""

    name = "movement_distance"

    def __init__(self, scale: int = DEFAULT_SCALE):
        self.scale = scale

    def extract(self, round_record: RoundRecord) -> dict[str, int]:
        total = 0
        for e in _event_fields(round_record.events, "move"):
            dist = e.get("distance", 0)
            if isinstance(dist, (int, float)) and not isinstance(dist, bool):
                total += to_fixed(float(dist), self.scale)
        return {"distance": total}


def default_features() -> list[Feature]:
    """内置特征集合。"""
    return [
        KillCountFeature(),
        TotalDamageFeature(),
        SkillUsageFeature(),
        MovementDistanceFeature(),
    ]
