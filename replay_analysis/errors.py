"""回放分析链路的错误类型。

加载阶段的失败原因必须互相可区分：
- UnsupportedVersionError: 版本不受支持
- CorruptedReplayError: 数据损坏（校验失败 / 非法内容）
- TruncatedReplayError: 数据截断（意外结束）
"""


class ReplayError(Exception):
    """所有回放分析错误的基类。"""


class ReplayFormatError(ReplayError):
    """回放产物格式层面的错误基类。"""


class UnsupportedVersionError(ReplayFormatError):
    """回放产物版本不被当前读取器支持。"""


class CorruptedReplayError(ReplayFormatError):
    """回放产物内容损坏（魔数错误、校验和不匹配、非法负载）。"""


class TruncatedReplayError(ReplayFormatError):
    """回放产物被截断（长度前缀声明的字节未完整到达）。"""
