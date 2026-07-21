"""
sink.py — 日志打印点 (Telemetry Sink) 模型

Sink 代表源码中一个日志打印语句的位置。在 GONDAR 框架中，sink 是
我们希望 fuzz 输入最终能"到达"的目标 — 当执行路径经过 sink 时，
说明用户可控的数据流入了遥测输出。

Sink 可以通过静态分析自动发现（未来），或者手工标注。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SinkType(str, Enum):
    """Sink 的语义类型."""
    LOG_ERROR = "log_error"       # log.error(...) — 错误级日志
    LOG_WARN = "log_warn"         # log.warn(...) — 警告级日志
    LOG_INFO = "log_info"         # log.info(...) — 信息级日志
    LOG_DEBUG = "log_debug"       # log.debug(...) — 调试级日志
    EXCEPTION_HANDLER = "exception_handler"  # 全局异常处理器中的日志
    CUSTOM = "custom"             # 自定义 sink (如封装的日志函数)


@dataclass
class Sink:
    """
    日志打印点 — 源码中一个日志语句的精确位置。

    标识一个我们关心的遥测数据产生点。fuzz 的目标是让执行路径
    经过这些 sink，使得攻击者可控的 HTTP 参数出现在日志输出中。
    """
    # ── 代码位置 ─────────────────────────────────────────────────
    class_name: str             # 完全限定类名, e.g. "com.example.service.UserService"
    method: str                 # 所在方法名, e.g. "findById"
    line_number: int = -1       # 日志语句行号
    src_file: str = ""          # 源文件名, e.g. "UserService.java"

    # ── Sink 语义 ────────────────────────────────────────────────
    sink_type: SinkType = SinkType.LOG_INFO
    log_message: str = ""       # 日志模板, e.g. "User not found: id={}"
    log_api: str = ""           # 日志 API, e.g. "log.warn"

    # ── 参数可控性 (静态分析标注) ────────────────────────────────
    tainted_params: list[str] = field(default_factory=list)
    # 哪些日志参数是从 HTTP 请求传入的, e.g. ["id"]

    @property
    def id(self) -> str:
        """唯一标识: class.method:line."""
        return f"{self.class_name}.{self.method}:{self.line_number}"

    @property
    def qualified_method(self) -> str:
        """class.method 格式."""
        return f"{self.class_name}.{self.method}"

    @property
    def severity(self) -> int:
        """Sink 严重级别 (越高越有价值). ERROR > WARN > INFO > DEBUG."""
        severity_map = {
            SinkType.LOG_ERROR: 4,
            SinkType.EXCEPTION_HANDLER: 4,
            SinkType.LOG_WARN: 3,
            SinkType.LOG_INFO: 2,
            SinkType.LOG_DEBUG: 1,
            SinkType.CUSTOM: 2,
        }
        return severity_map.get(self.sink_type, 1)

    def __str__(self) -> str:
        return f"[{self.sink_type.value}] {self.qualified_method}:{self.line_number} — {self.log_message}"