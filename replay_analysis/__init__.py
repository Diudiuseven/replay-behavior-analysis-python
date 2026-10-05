"""面向对局复盘的流式回放行为分析包。"""

from .errors import (
    CorruptDataError,
    ReplayFormatError,
    SegmentTooLargeError,
    TruncatedDataError,
    UnsupportedVersionError,
)

__all__ = [
    "CorruptDataError",
    "ReplayFormatError",
    "SegmentTooLargeError",
    "TruncatedDataError",
    "UnsupportedVersionError",
]

from .aggregator import Aggregator, Snapshot
from .engine import ReplayEngine
from .features import (
    Feature,
    KillCountFeature,
    MovementDistanceFeature,
    SkillUsageFeature,
    TotalDamageFeature,
    default_features,
)
from .format import RoundRecord, Segment

__all__ += [
    "Aggregator",
    "Feature",
    "KillCountFeature",
    "MovementDistanceFeature",
    "ReplayEngine",
    "RoundRecord",
    "Segment",
    "SkillUsageFeature",
    "Snapshot",
    "TotalDamageFeature",
    "default_features",
]
