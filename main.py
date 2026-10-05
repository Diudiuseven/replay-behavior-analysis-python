"""回放行为分析演示：纯进程内运行，输入用本地文件模拟。

流程：生成回放产物 -> 流式读取 -> 乱序/重复摄入 -> 更正 -> 查询快照。
"""
from __future__ import annotations

import random
import tempfile
from pathlib import Path

from replay_analysis import ReplayAnalyzer, ReplayReader, ReplayWriter, Round, RoundEvent, Segment


def build_demo_replay(path: Path, replay_id: str, segments: int, rounds_per_segment: int) -> None:
    rng = random.Random(42)
    with path.open("wb") as fh:
        writer = ReplayWriter(fh)
        for seg_idx in range(segments):
            rounds = []
            for j in range(rounds_per_segment):
                round_id = seg_idx * rounds_per_segment + j
                events = tuple(
                    RoundEvent("kill") for _ in range(rng.randint(0, 3))
                ) + (
                    RoundEvent("damage", {"amount": rng.randint(0, 500)}),
                    RoundEvent("gold_earned", {"amount": rng.randint(100, 400)}),
                    RoundEvent("move", {"distance": rng.uniform(5, 60)}),
                )
                rounds.append(Round(round_id, events))
            writer.write_segment(Segment(replay_id, seg_idx, tuple(rounds)))
        writer.finish()


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="replay-demo-"))
    replay_path = work / "demo.rpl"
    build_demo_replay(replay_path, "demo-match", segments=6, rounds_per_segment=10)

    with ReplayAnalyzer("demo-match", work_dir=work / "state") as analyzer:
        with replay_path.open("rb") as fh:
            segments = list(ReplayReader(fh).segments())  # 演示方便；实际可边读边提交
        shuffled = sorted(segments, key=lambda s: -s.segment_index)  # 乱序到达
        for seg in shuffled:
            analyzer.submit_segment(seg)
        analyzer.submit_segment(shuffled[0])  # 重复段：被忽略

        snap = analyzer.query()
        print(f"版本={snap.version} 回合数={snap.rounds_applied}")
        for feature, metrics in snap.as_dict().items():
            print(f"  {feature}: {metrics}")

        # 更正第 3 回合并重查
        analyzer.correct_round(Round(3, (RoundEvent("kill"), RoundEvent("kill"))))
        print(f"更正后版本={analyzer.query().version} kills={analyzer.query().feature('combat')['kills']}")


if __name__ == "__main__":
    main()
