"""回放分析链路的错误类型。

加载阶段的失败原因必须互相可区分：
- UnsupportedVersionError: 版本不受支持（含未声明兼容的新版本）
- CorruptDataError: 数据损坏（magic/校验和/JSON 非法、段过大等）
- TruncatedDataError: 输入被截断（声明长度大于实际可读字节）
"""


class ReplayFormatError(Exception):
    """回放产物格式错误的基类。"""


class UnsupportedVersionError(ReplayFormatError):
    """产物版本不被当前实现支持，或未声明与当前实现兼容。"""

    def __init__(self, version: int, min_reader_version: int, supported: int):
        self.version = version
        self.min_reader_version = min_reader_version
        self.supported = supported
        super().__init__(
            f"unsupported replay version: format={version} "
            f"min_reader={min_reader_version} supported<={supported}"
        )


class CorruptDataError(ReplayFormatError):
    """数据损坏：magic 错误、校验和不匹配、JSON 非法、字段缺失等。"""


class TruncatedDataError(ReplayFormatError):
    """输入截断：帧头声明的长度超过实际剩余字节。"""


class SegmentTooLargeError(CorruptDataError):
    """单个段超过配置的最大字节数（防止内存被单段撑爆）。"""

    def __init__(self, size: int, limit: int):
        self.size = size
        self.limit = limit
        super().__init__(f"segment size {size} exceeds limit {limit}")
