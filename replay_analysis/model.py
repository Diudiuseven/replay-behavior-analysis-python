"""回放数据模型：事件、回合、分段。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RoundEvent:
    """回合内的单条行为事件。"""

    kind: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Round:
    """一个回合（对局中的最小分析单元）。"""

    round_id: int
    events: tuple[RoundEvent, ...] = ()


@dataclass(frozen=True)
class Segment:
    """回放的一个分段，可乱序、可重复到达。"""

    replay_id: str
    segment_index: int
    rounds: tuple[Round, ...] = ()
