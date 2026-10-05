"""端到端演示：构造分段回放 -> 乱序+重复加载 -> 更正 -> 快照查询。"""

import io

from replay_analysis import ReplayEngine, RoundRecord, Segment
from replay_analysis.format import write_header, write_segment


def build_replay() -> bytes:
    buf = io.BytesIO()
    write_header(buf)
    for i in range(10):
        events = tuple(
            [{"type": "kill"}] * (i % 3)
            + [{"type": "damage", "amount": 100 * (i % 4)}]
            + [{"type": "move", "distance": 3.5}]
        )
        write_segment(buf, Segment(f"seg-{i}", (RoundRecord(f"round-{i}", 1, i, events),)))
    return buf.getvalue()


def main() -> None:
    data = build_replay()
    engine = ReplayEngine()

    # 模拟乱序 + 重复到达：把字节流切成两份乱序喂入
    engine.load_stream(io.BytesIO(data))
    engine.load_stream(io.BytesIO(data))  # 整体重复，不得重复计入
    print("after load:", dict(engine.snapshot().totals))

    # 更正 round-3：击杀数改为 5
    engine.submit_round(
        RoundRecord("round-3", 2, 3, tuple({"type": "kill"} for _ in range(5)))
    )
    snap = engine.snapshot()
    print(f"version={snap.version} kill_count={snap.query('kill_count')}")
    engine.close()


if __name__ == "__main__":
    main()
