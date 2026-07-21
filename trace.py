"""
trace.py — 完整的请求级追踪模型

映射 HTTP 响应头中的两条追踪路径:
  1. X-Execution-Trace  → 方法级调用栈 (SpanRecord JSON 数组)
  2. X-Coverage-Data    → 行级覆盖率 (JaCoCo .exec 二进制)

两者合并后构成一次请求的完整执行路径:
  方法调用树 + 每个类/方法内实际执行的源码行号
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Optional

from sink import Sink
from source import Source


# ═══════════════════════════════════════════════════════════════════════
# 方法级追踪节点 — 对应 X-Execution-Trace 中的单个 SpanRecord
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class TraceNode:
    """单个方法执行 span, 直接映射 Java 端 SpanRecord 的全部字段."""

    # ── identity ──────────────────────────────────────────────────
    span_id: str            # 16 位 hex span ID
    parent_span_id: str     # 父 span ID; 根节点为空串
    trace_id: str           # 32 位 hex trace ID (同一请求内共享)

    # ── code location ─────────────────────────────────────────────
    content: str            # span 名称,  e.g. "AppController.getUser"
    function: str           # 方法名,     e.g. "getUser"
    method_signature: str   # 含参数类型,  e.g. "getUser(Long)"
    class_namespace: str    # 完全限定类名, e.g. "com.example.controller.AppController"
    src_file: str           # 源文件名,   e.g. "AppController.java"
    line_number: int        # 方法体首行行号 (来自字节码 LineNumberTable), -1 表示未知

    # ── timing ────────────────────────────────────────────────────
    start_ns: int           # Unix epoch 纳秒时间戳
    duration_ns: int        # 方法实际耗时 (ns)

    # ── status ────────────────────────────────────────────────────
    is_error: bool = False  # 该 span 是否以 ERROR 状态结束
    error_message: Optional[str] = None  # 异常消息, 仅 is_error=True 时非空

    # ── 运行时变量快照 (仅在 snapshot 模式下填充) ─────────────────
    args_snapshot: Optional[dict] = None
    # 方法参数值快照, e.g. {"arg0": "hello", "arg1": 42}

    return_snapshot: Optional[dict] = None
    # 返回值快照, e.g. {"type": "String", "value": "ok"}

    this_snapshot: Optional[dict] = None
    # this 对象字段快照, e.g. {"_class": "UserService", "userDao": "..."}

    # ── statement-level coverage (从 JaCoCo 解析后回填) ────────────
    executed_lines: list[int] = field(default_factory=list)
    # 该方法内实际执行的源码行号列表

    # ── 层级关系 (构建树后填充) ────────────────────────────────────
    children: list[TraceNode] = field(default_factory=list, repr=False)


# ═══════════════════════════════════════════════════════════════════════
# 行级覆盖率数据 — 对应 X-Coverage-Data (JaCoCo .exec 解析结果)
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class LineCoverage:
    """单个源文件的行级覆盖详情."""
    class_name: str             # 完全限定类名, e.g. "com/example/controller/AppController"
    src_file: str               # 源文件名,   e.g. "AppController.java"
    covered_lines: list[int] = field(default_factory=list)    # 已执行的行号
    missed_lines: list[int] = field(default_factory=list)     # 未执行的行号


@dataclass
class CoverageData:
    """一次请求的完整 JaCoCo 覆盖率快照."""
    raw_exec: bytes = field(default=b"", repr=False)
    # 原始 .exec 二进制 (可用 jacococli 生成报告)

    class_coverages: list[LineCoverage] = field(default_factory=list)
    # 解析后的类级行覆盖列表 (需配合源码/class文件解析)

    @property
    def total_covered(self) -> int:
        return sum(len(c.covered_lines) for c in self.class_coverages)

    @property
    def total_missed(self) -> int:
        return sum(len(c.missed_lines) for c in self.class_coverages)

    @property
    def line_coverage_rate(self) -> float:
        total = self.total_covered + self.total_missed
        return self.total_covered / total if total > 0 else 0.0


# ═══════════════════════════════════════════════════════════════════════
# 完整请求追踪 — 聚合两条追踪路径
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class Trace:
    """
    一次 HTTP 请求的完整执行追踪, 包含:
      - source: 请求入口信息 (RESTful / RPC)
      - sink:   响应出口信息
      - nodes:  方法级调用栈 (来自 X-Execution-Trace)
      - coverage: 行级覆盖 (来自 X-Coverage-Data)
      - root_nodes: 调用树的根节点列表 (由 build_tree 构建)
    """
    source: Source
    sink: Sink
    nodes: list[TraceNode] = field(default_factory=list)
    coverage: Optional[CoverageData] = None
    root_nodes: list[TraceNode] = field(default_factory=list)

    # ── 原始响应头 (方便调试/重放) ────────────────────────────────
    raw_trace_header: Optional[str] = None      # X-Execution-Trace 原始值
    raw_coverage_header: Optional[str] = None   # X-Coverage-Data 原始值

    def build_tree(self) -> list[TraceNode]:
        """根据 span_id / parent_span_id 构建调用树, 返回根节点列表."""
        node_map: dict[str, TraceNode] = {n.span_id: n for n in self.nodes}
        roots: list[TraceNode] = []
        for node in self.nodes:
            node.children = []  # reset
        for node in self.nodes:
            if node.parent_span_id and node.parent_span_id in node_map:
                node_map[node.parent_span_id].children.append(node)
            else:
                roots.append(node)
        # 按 start_ns 排序
        roots.sort(key=lambda n: n.start_ns)
        for node in self.nodes:
            node.children.sort(key=lambda n: n.start_ns)
        self.root_nodes = roots
        return roots

    @staticmethod
    def parse_execution_trace(header_value: str) -> list[TraceNode]:
        """
        解析 X-Execution-Trace 响应头值为 TraceNode 列表.

        header_value: Base64 编码的 JSON 数组, 或 "IN_BODY" (需从 body 获取)
        """
        if not header_value or header_value == "IN_BODY":
            return []

        json_bytes = base64.b64decode(header_value)
        spans: list[dict] = json.loads(json_bytes)
        nodes: list[TraceNode] = []
        for s in spans:
            nodes.append(TraceNode(
                span_id=s.get("span_id", ""),
                parent_span_id=s.get("parent_span_id", ""),
                trace_id=s.get("trace_id", ""),
                content=s.get("content", ""),
                function=s.get("function", ""),
                method_signature=s.get("method_signature", ""),
                class_namespace=s.get("class_namespace", ""),
                src_file=s.get("src_file", ""),
                line_number=s.get("line_number", -1),
                start_ns=s.get("start_ns", 0),
                duration_ns=s.get("duration_ns", 0),
                is_error=s.get("is_error", False),
                error_message=s.get("error_message"),
                args_snapshot=s.get("args_snapshot"),
                return_snapshot=s.get("return_snapshot"),
                this_snapshot=s.get("this_snapshot"),
            ))
        return nodes

    @staticmethod
    def parse_coverage_data(header_value: str) -> Optional[CoverageData]:
        """
        解析 X-Coverage-Data 响应头值为 CoverageData.

        header_value: Base64 编码的 JaCoCo .exec 二进制, 或 "IN_BODY"
        返回 CoverageData (raw_exec 已填充, class_coverages 需后续解析)
        """
        if not header_value or header_value == "IN_BODY":
            return None

        raw_exec = base64.b64decode(header_value)
        return CoverageData(raw_exec=raw_exec)

    @classmethod
    def from_response_headers(
        cls,
        source: Source,
        sink: Sink,
        trace_header: Optional[str] = None,
        coverage_header: Optional[str] = None,
    ) -> Trace:
        """
        从 HTTP 响应头直接构建 Trace 对象.

        Args:
            source: 请求源信息
            sink: 响应出口信息
            trace_header: X-Execution-Trace 响应头的值
            coverage_header: X-Coverage-Data 响应头的值

        Returns:
            完整的 Trace 对象, 已构建调用树
        """
        nodes = cls.parse_execution_trace(trace_header) if trace_header else []
        coverage = cls.parse_coverage_data(coverage_header) if coverage_header else None

        trace = cls(
            source=source,
            sink=sink,
            nodes=nodes,
            coverage=coverage,
            raw_trace_header=trace_header,
            raw_coverage_header=coverage_header,
        )
        trace.build_tree()
        return trace