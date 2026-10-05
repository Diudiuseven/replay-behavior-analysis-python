"""每回合特征贡献的存储。

聚合器需要按回合取回旧贡献以支持更正（减旧加新）。
- ContributionStore: 纯内存实现，适合小回放与测试；
- SqliteContributionStore: 落盘实现，内存占用不随回放长度增长，
  满足大体量回放的流式处理约束（仅依赖标准库 sqlite3，非外部服务）。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path


def _dump(contribution: dict[str, dict[str, float]]) -> str:
    return json.dumps(contribution, sort_keys=True, separators=(",", ":"))


def _load(raw: str) -> dict[str, dict[str, float]]:
    data = json.loads(raw)
    return {k: {m: float(v) for m, v in metrics.items()} for k, metrics in data.items()}


class ContributionStore:
    """内存实现（适合小回放与测试）。"""

    def __init__(self) -> None:
        self._data: dict[int, dict[str, dict[str, float]]] = {}

    def get(self, round_id: int) -> dict[str, dict[str, float]] | None:
        return self._data.get(round_id)

    def put(self, round_id: int, contribution: dict[str, dict[str, float]]) -> None:
        self._data[round_id] = contribution

    def close(self) -> None:
        self._data.clear()


class SqliteContributionStore(ContributionStore):
    """落盘实现：内存占用不随回放长度增长。"""

    def __init__(self, path: str | Path) -> None:
        # check_same_thread=False：由上层聚合器的锁保证串行访问。
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS contributions "
            "(round_id INTEGER PRIMARY KEY, payload TEXT NOT NULL)"
        )
        self._conn.commit()
        self._pending = 0
        # 批量提交：避免每个回合一次 fsync，close 时保证落盘。
        self._commit_every = 512

    def get(self, round_id: int) -> dict[str, dict[str, float]] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM contributions WHERE round_id = ?", (round_id,)
            ).fetchone()
        return _load(row[0]) if row else None

    def put(self, round_id: int, contribution: dict[str, dict[str, float]]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO contributions (round_id, payload) VALUES (?, ?)",
                (round_id, _dump(contribution)),
            )
            self._pending += 1
            if self._pending >= self._commit_every:
                self._conn.commit()
                self._pending = 0

    def close(self) -> None:
        with self._lock:
            self._conn.commit()
            self._conn.close()
