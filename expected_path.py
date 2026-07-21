"""
expected_path.py — 预期执行路径模型

表示从一个 API 入口到一个日志打印点 (sink) 的静态预期调用路径.
该路径由静态分析 (调用图 + 污点分析) 产生, 粒度为方法级.

用途:
  - fuzz 时发真实请求获得实际 Trace
  - 将实际 Trace 与 ExpectedPath 做偏差比较
  - 定位执行流偏离预期路径的第一个节点 (Reachability Progress)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


class PathSource(str, Enum):
    """路径发现方式."""
    TAINT = "taint"         # 通过污点分析发现 (高置信度)
    CALL_GRAPH = "call_graph"  # 仅通过调用图发现 (可能有误报)


@dataclass
class PathNode:
    """
    预期路径上的一个节点 — 对应一次方法调用.

    粒度: 方法级 (class + method).
    这是静态分析的产物, 没有时间戳, 只有拓扑顺序.
    """
    # ── 代码位置 ─────────────────────────────────────────────────
    class_name: str         # 完全限定类名, e.g. "com.example.service.UserService"
    method: str             # 方法名, e.g. "getUser"
    method_signature: str = ""  # 含参数类型, e.g. "getUser(Long)" (可选)
    src_file: str = ""      # 源文件名, e.g. "UserService.java"
    line_number: int = -1   # 方法入口行号 (-1 表示未知)

    # ── 路径中的位置 ─────────────────────────────────────────────
    depth: int = 0          # 在调用路径中的深度 (0 = API 入口)

    @property
    def qualified_name(self) -> str:
        """返回 class.method 格式的全限定方法名."""
        return f"{self.class_name}.{self.method}"

    def matches_trace_node(self, class_namespace: str, function: str) -> bool:
        """
        判断该 PathNode 是否匹配一个动态 TraceNode.

        匹配策略: class_name 和 method 都相等.
        class_name 格式统一为 '.' 分隔.
        """
        # 标准化: 将 '/' 替换为 '.'
        normalized_class = class_namespace.replace('/', '.')
        self_class = self.class_name.replace('/', '.')
        return self_class == normalized_class and self.method == function


@dataclass
class LogSink:
    """
    日志打印点 — 项目中的一个 log 语句位置.

    代表一个我们关心的 sink: 当执行到达这里时, 会产生日志输出.
    """
    # ── 位置 ─────────────────────────────────────────────────────
    class_name: str         # 完全限定类名
    method: str             # 所在方法名
    src_file: str = ""      # 源文件名
    line_number: int = -1   # 日志语句行号

    # ── 日志信息 ─────────────────────────────────────────────────
    log_level: str = ""     # "INFO", "WARN", "ERROR", "DEBUG" 等
    log_message_template: str = ""  # 日志消息模板 (静态部分)
    log_api: str = ""       # 日志 API, e.g. "log.info", "logger.error"

    @property
    def id(self) -> str:
        """唯一标识: class:method:line."""
        return f"{self.class_name}:{self.method}:{self.line_number}"


@dataclass
class APIEntry:
    """
    API 入口点 — 项目中的一个 REST/RPC 端点.

    代表一个可以被外部调用的接口方法.
    """
    # ── 位置 ─────────────────────────────────────────────────────
    class_name: str         # Controller 类名
    method: str             # Handler 方法名
    src_file: str = ""      # 源文件名
    line_number: int = -1   # 方法入口行号

    # ── HTTP 信息 ─────────────────────────────────────────────────
    http_method: str = ""   # "GET", "POST", "PUT", "DELETE" 等
    http_path: str = ""     # URL 路径, e.g. "/api/users/{id}"

    @property
    def id(self) -> str:
        """唯一标识: HTTP_METHOD path 或 class.method."""
        if self.http_method and self.http_path:
            return f"{self.http_method} {self.http_path}"
        return f"{self.class_name}.{self.method}"


@dataclass
class ExpectedPath:
    """
    从 API 入口到日志打印点的一条预期执行路径.

    由静态分析产生, 表示 "如果请求从 api_entry 进入,
    要到达 log_sink, 预期会经过 nodes 这些方法调用".

    属性:
      - api_entry: 路径起点 (API 入口)
      - log_sink:  路径终点 (日志打印点)
      - nodes:     有序的方法调用序列 (从 entry 到 sink)
      - source:    路径发现方式 (taint / call_graph)
      - confidence: 路径置信度 (taint > call_graph)
    """
    # ── 两端 ─────────────────────────────────────────────────────
    api_entry: APIEntry     # 路径起点
    log_sink: LogSink       # 路径终点

    # ── 路径节点序列 ─────────────────────────────────────────────
    nodes: list[PathNode] = field(default_factory=list)
    # 有序调用序列: nodes[0] = entry method, nodes[-1] = sink method
    # 中间是从 entry 到 sink 经过的所有方法

    # ── 元信息 ───────────────────────────────────────────────────
    source: PathSource = PathSource.CALL_GRAPH
    confidence: float = 0.5  # [0, 1], taint 路径默认 0.8, CG 路径默认 0.5
    path_length: int = 0     # 路径长度 (= len(nodes))

    def __post_init__(self):
        self.path_length = len(self.nodes)
        # 根据 source 设置默认 confidence
        if self.confidence == 0.5 and self.source == PathSource.TAINT:
            self.confidence = 0.8

    @property
    def id(self) -> str:
        """路径唯一标识: entry_id → sink_id."""
        sink_id = self.log_sink.id if self.log_sink else "?"
        return f"{self.api_entry.id} → {sink_id}"

    @property
    def method_sequence(self) -> list[str]:
        """返回方法级调用序列 (class.method 格式)."""
        return [node.qualified_name for node in self.nodes]

    def node_at_depth(self, depth: int) -> Optional[PathNode]:
        """获取指定深度的节点."""
        if 0 <= depth < len(self.nodes):
            return self.nodes[depth]
        return None


@dataclass
class ExpectedPathSet:
    """
    一个项目的完整预期路径集合: API × LogSink 的路径矩阵.

    组织方式:
      - by_api[api_id] → 该 API 能到达的所有路径
      - by_sink[sink_id] → 能到达该 sink 的所有路径
      - all_paths → 全部路径列表
    """
    project_name: str = ""
    all_paths: list[ExpectedPath] = field(default_factory=list)

    # ── 索引 (延迟构建) ──────────────────────────────────────────
    _by_api: dict[str, list[ExpectedPath]] = field(
        default_factory=dict, repr=False
    )
    _by_sink: dict[str, list[ExpectedPath]] = field(
        default_factory=dict, repr=False
    )

    def build_index(self) -> None:
        """构建 by_api / by_sink 索引."""
        self._by_api.clear()
        self._by_sink.clear()
        for path in self.all_paths:
            api_id = path.api_entry.id
            sink_id = path.log_sink.id
            self._by_api.setdefault(api_id, []).append(path)
            self._by_sink.setdefault(sink_id, []).append(path)

    def paths_for_api(self, api_id: str) -> list[ExpectedPath]:
        """获取某个 API 的所有预期路径."""
        if not self._by_api:
            self.build_index()
        return self._by_api.get(api_id, [])

    def paths_for_sink(self, sink_id: str) -> list[ExpectedPath]:
        """获取能到达某个 sink 的所有预期路径."""
        if not self._by_sink:
            self.build_index()
        return self._by_sink.get(sink_id, [])

    @property
    def api_count(self) -> int:
        if not self._by_api:
            self.build_index()
        return len(self._by_api)

    @property
    def sink_count(self) -> int:
        if not self._by_sink:
            self.build_index()
        return len(self._by_sink)

    @property
    def stats(self) -> str:
        """统计摘要."""
        return (
            f"Project: {self.project_name} | "
            f"APIs: {self.api_count} | "
            f"Sinks: {self.sink_count} | "
            f"Paths: {len(self.all_paths)} | "
            f"Taint: {sum(1 for p in self.all_paths if p.source == PathSource.TAINT)} | "
            f"CG: {sum(1 for p in self.all_paths if p.source == PathSource.CALL_GRAPH)}"
        )
