from dataclasses import dataclass, field

from sink import Sink
from source import Source

@dataclass
class TraceNode():
    # ── source location ───────────────────────────────────────────
    src_file: str           # 源文件名,   e.g. "AppController.java"
    line_number: int        # 方法体首行行号

    # ── identity ──────────────────────────────────────────────────
    content: str            # span 名称,  e.g. "AppController.getUser"
    function: str           # 方法名,     e.g. "getUser"
    method_signature: str   # 含参数类型,  e.g. "getUser(Long)"
    class_namespace: str    # 完全限定类名, e.g. "com.example...AppController"

    # ── call-tree links ───────────────────────────────────────────
    span_id: str            # OTel span ID (16 位 hex)
    parent_span_id: str     # 父 span ID;  根节点为空串

    # ── timing ────────────────────────────────────────────────────
    start_ns: int           # Unix epoch 纳秒时间戳
    duration_ns: int        # 方法实际耗时 (ns)

    # ── status ────────────────────────────────────────────────────
    is_error: bool          # 该 span 是否以 ERROR 状态结束

    # ── statement-level coverage (JaCoCo) ────────────────────────
    executed_lines: list[int] = field(default_factory=list)
    # 该方法内实际执行的源码行号列表（由 JacocoTracer 填充）

@dataclass
class Trace():
    source: Source
    sink: Sink
    nodes: list[TraceNode]