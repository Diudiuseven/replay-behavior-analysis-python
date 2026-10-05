# replay-behavior-analysis-python

面向对局复盘的流式回放行为分析。回放数据分段产生、可乱序到达，
同一回合可被重新分析或更正；整条链路在单进程内运行，输入用
内存或本地文件模拟，不依赖外部服务。

## 快速开始

```python
from replay_analysis import ReplayEngine

engine = ReplayEngine()                 # 内置四个特征
engine.load_file("match.rply")          # 流式加载，不整份读入内存
engine.submit_round(corrected_round)    # 更正某一回合（更高 revision）
snap = engine.snapshot()                # 当前已提交的完整聚合版本
print(snap.version, snap.query("kill_count"))
engine.close()
```

运行端到端演示：`python main.py`；运行测试：`pytest`。

## 回放产物格式与版本策略

二进制布局（小端）：

```
头部:  magic(4B)="RPLY" | format_version(u16) | min_reader_version(u16)
段帧:  payload_len(u32) | crc32(payload)(u32) | payload(JSON UTF-8)
```

段 payload：`{"segment_id": str, "rounds": [{"round_id", "revision",
"tick", "events"}]}`。

版本策略：

- 当前实现支持读取 `format_version` 1..2（`SUPPORTED_VERSION = 2`）；
- 产物通过 `min_reader_version` 声明兼容性，仅当
  `1 <= format_version <= 2` 且 `min_reader_version <= 2` 时可读；
- v1 的 round 允许缺省 `revision` 字段，读取时按 1 补齐，
  因此声明兼容的旧版本产物可继续分析；
- 加载阶段的失败原因互相可区分，且不会崩溃或继续使用错误数据：
  - `UnsupportedVersionError`：版本不受支持 / 未声明兼容；
  - `CorruptDataError`：magic 错误、CRC 不匹配、JSON 非法、
    字段缺失等（`SegmentTooLargeError` 是其子类，单段超限）；
  - `TruncatedDataError`：帧头声明长度超过实际剩余字节。

## 聚合语义

- **段去重**：段按 `segment_id` 去重，重复段与迟到段直接忽略，
  不会被重复计入；乱序到达与按顺序单次处理的结果完全一致。
- **按回合更正**：每回合只保留最高 `revision` 的贡献，
  聚合结果 = Σ 各回合最新贡献。更正（更高 revision）= 减旧贡献 +
  加新贡献，结果与从头重算逐项一致，无重复累加、无残留旧值。
- **幂等重算**：同 `revision` 重复提交是空操作（`duplicate`）；
  更低 `revision` 的迟到提交被忽略（`stale`）。同 `revision`
  但数据冲突时保留先到者。
- **可复现性**：特征贡献一律为定点整数（浮点按 `scale` 缩放，
  默认 1000），加法聚合满足交换律/结合律；相同输入与相同分段
  顺序下输出逐条一致，与段到达顺序无关。
- **特征隔离**：各特征独立提取、独立命名空间（`feature.metric`），
  新增特征不改变已有特征的结果。

## 内存边界

解析逐段流式进行，单次分析不会把整份回放载入内存。每回合贡献与
段去重集合存放在 sqlite（派生缓存，崩溃后重放即可恢复，故关闭
synchronous 刷盘），进程堆内只保留 O(特征数 × 指标数) 的运行
合计，内存占用不随回放长度无界增长。

## 并发语义

分析、查询、更正可并发进行：

- 所有变更在内部锁下串行提交，每次提交产生单调递增的版本号；
- 查询拿到的是某个**已提交的完整聚合版本**的不可变 `Snapshot`，
  不会观察到中间态；
- 慢查询只持有旧快照引用，不阻塞分析推进；旧快照内容永远不变。

## 配置项

`ReplayEngine(features=None, *, db_path=":memory:", max_segment_bytes=16MiB)`

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `features` | 内置四特征 | 特征列表；新增特征实现 `Feature.extract` 后传入即可 |
| `db_path` | `:memory:` | 每回合贡献/段去重的存储位置；大体量回放传文件路径以落盘 |
| `max_segment_bytes` | 16 MiB | 单段最大字节数，超限抛 `SegmentTooLargeError` |

内置特征：`kill_count`（击杀数）、`total_damage`（伤害总量）、
`skill_usage`（技能使用，含按技能细分）、`movement_distance`
（移动距离，定点化，`scale` 可调）。

## 目录结构

```
replay_analysis/
  errors.py      # 可区分的加载错误类型
  format.py      # 带版本的帧格式解析/写入（流式）
  features.py    # 可插拔行为特征
  aggregator.py  # 段去重、按回合更正、MVCC 快照
  engine.py      # 流式加载 + 查询/更正统一入口
tests/           # 乱序/重复/更正重算/版本兼容/损坏输入/并发
```
