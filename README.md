# 回放行为分析（replay-behavior-analysis）

面向对局复盘的行为特征聚合系统。回放数据分段产生、可乱序到达，同一回合可被重新分析或更正；整条链路纯进程内运行，输入用内存或本地文件模拟，不依赖外部服务。

## 快速开始

```bash
uv run python main.py   # 演示：生成回放 -> 流式读取 -> 乱序摄入 -> 更正 -> 查询
uv run pytest           # 运行全部自动化测试
```

```python
from replay_analysis import ReplayAnalyzer, ReplayReader, Round, RoundEvent, Segment

with ReplayAnalyzer("match-1", work_dir="./state") as analyzer:
    with open("replay.rpl", "rb") as fh:
        for segment in ReplayReader(fh).segments():  # 流式读取，不整份载入
            analyzer.submit_segment(segment)          # 可乱序、可重复
    analyzer.correct_round(Round(3, (RoundEvent("kill"),)))  # 更正第 3 回合
    snapshot = analyzer.query()                       # 获取已提交快照
    print(snapshot.version, snapshot.as_dict())
```

## 聚合语义

- **按回合替换式聚合**：每个回合（以 `round_id` 标识）的特征贡献独立计算并保存；
  合计 = 所有 `round_id` 最终贡献的逐项求和。
- **幂等重算**：同一回合以相同内容重复到达时不产生任何变更（不重复累加、版本号不变）。
- **更正**：同一回合以不同内容到达时，先撤销旧贡献再累加新贡献，不留残值；
  更正后的聚合结果与从头重算逐项一致。
- **顺序无关**：合计只依赖"每个回合的最终内容"，与分段到达顺序无关；
  乱序、重复、迟到分段与按顺序单次处理的结果完全一致。
- **分段去重**：按 `(segment_index, 内容哈希)` 去重；同索引同内容直接忽略，
  同索引不同内容视为对所含回合的更正。
- **快照隔离**：每次 `submit_segment` / `correct_round` 提交一个不可变 `Snapshot`；
  查询永远读到某个已提交的完整版本，慢查询只持有引用，不阻塞分析推进。

## 回放产物格式与版本策略

二进制布局：

```
头部: MAGIC("RPLY") | format_version(u16) | min_reader_version(u16)
帧:   payload_length(u32) | crc32(u32) | payload(JSON)
末帧: {"type": "end"}
```

- 当前写入版本为 **2**，读取器支持版本 **1 和 2**（`SUPPORTED_VERSIONS`）；
  v2 回合允许附加字段（如 `timestamp`），读取时忽略，保证旧产物可继续分析。
- 产物声明 `min_reader_version`，读取器版本低于它时拒绝加载。
- 加载阶段的失败原因互相可区分，且不会崩溃或继续使用错误数据：

| 异常 | 含义 |
| --- | --- |
| `UnsupportedVersionError` | 版本不在支持范围，或 `min_reader_version` 高于当前读取器 |
| `CorruptedReplayError` | 魔数错误、CRC 校验失败、非法 JSON / 非法字段 |
| `TruncatedReplayError` | 头部/帧前缀/负载被截断、缺少结束帧 |

## 特征扩展

特征是纯函数 `Round -> {指标名: 数值}`，注册到 `FeatureRegistry`：

```python
from replay_analysis import default_registry

registry = default_registry()  # 内置 combat / economy / movement / activity
registry.register("objective", lambda r: {"towers": sum(1.0 for e in r.events if e.kind == "tower")})
analyzer = ReplayAnalyzer("m", registry=registry)
```

- 各特征在独立命名空间下聚合，**新增特征不改变已有特征的结果**；
- 同一输入与同一分段顺序下，输出按键排序、逐条一致（确定性）；
- 特征函数必须只依赖回合内容本身，保证重算幂等。

## 配置项

| 参数 | 位置 | 说明 |
| --- | --- | --- |
| `work_dir` | `ReplayAnalyzer(...)` | 状态目录；贡献数据落盘为 `contributions.db`（SQLite，标准库实现）。缺省使用临时目录。 |
| `registry` | `ReplayAnalyzer(...)` | 特征注册表，缺省为 `default_registry()`。 |
| `version` | `ReplayWriter(...)` | 写入的产物版本（1 或 2），缺省为最新版。 |
| `_commit_every` | `SqliteContributionStore` | SQLite 批量提交间隔（默认 512 次写入），`close()` 时保证落盘。 |

## 内存与流式约束

- `ReplayReader` 逐帧解析，产出第一个分段前不会读取后续数据；
- 聚合状态大小只与特征数量相关；每回合贡献保存在 SQLite 中，
  内存占用不随回放长度无界增长（有自动化测试验证 40 倍数据量下峰值内存有界）。

## 并发模型

- `submit_segment` / `correct_round` 在内部锁下串行提交；
- `query()` 只读取最新不可变快照，分析与查询、更正可并发进行；
- 任一时刻查询到的都是某个已提交的完整聚合版本（跨特征自洽）。

## 目录结构

```
replay_analysis/
  format.py      # 带版本的产物读写（流式）
  model.py       # RoundEvent / Round / Segment
  features.py    # 特征注册表与内置特征
  store.py       # 每回合贡献存储（内存 / SQLite）
  aggregator.py  # 增量聚合与不可变快照
  analyzer.py    # 分段摄入、去重、更正、并发查询
  errors.py      # 可区分的加载错误类型
tests/           # 乱序、重复、更正重算、版本兼容、损坏输入、流式、并发
```
