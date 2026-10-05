"""面向对局复盘的回放行为分析（纯进程内、流式、可更正）。"""
from replay_analysis.analyzer import ReplayAnalyzer
from replay_analysis.aggregator import Aggregator, Snapshot
from replay_analysis.errors import (
    CorruptedReplayError,
    ReplayError,
    ReplayFormatError,
    TruncatedReplayError,
    UnsupportedVersionError,
)
from replay_analysis.features import FeatureRegistry, default_registry
from replay_analysis.format import ReplayReader, ReplayWriter
from replay_analysis.model import Round, RoundEvent, Segment
from replay_analysis.store import ContributionStore, SqliteContributionStore

__all__ = [
    "Aggregator",
    "ContributionStore",
    "CorruptedReplayError",
    "FeatureRegistry",
    "ReplayAnalyzer",
    "ReplayError",
    "ReplayFormatError",
    "ReplayReader",
    "ReplayWriter",
    "Round",
    "RoundEvent",
    "Segment",
    "Snapshot",
    "SqliteContributionStore",
    "TruncatedReplayError",
    "UnsupportedVersionError",
    "default_registry",
]
