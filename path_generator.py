"""
path_generator.py — 预期路径生成器

给定一个项目的:
  - API 入口列表 (APIEntry)
  - 日志打印点列表 (LogSink)
  - 调用图 (CallGraph)

生成 API × LogSink 的预期执行路径矩阵 (ExpectedPathSet).

算法 (参考 GONDAR §3.2):
  1. 从调用图中, 为每个 (api_entry, log_sink) 对查找所有路径
  2. 按优先级选择代表性路径:
     - Taint 路径 > Call Graph 路径
     - 短路径 > 长路径
  3. 输出 ExpectedPathSet
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from collections import defaultdict, deque

from expected_path import (
    APIEntry,
    LogSink,
    PathNode,
    ExpectedPath,
    ExpectedPathSet,
    PathSource,
)


# ═══════════════════════════════════════════════════════════════════════
# 调用图模型
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class CallGraphNode:
    """调用图中的一个节点 (一个方法)."""
    class_name: str         # 完全限定类名
    method: str             # 方法名
    method_signature: str = ""
    src_file: str = ""
    line_number: int = -1

    @property
    def id(self) -> str:
        return f"{self.class_name}.{self.method}"


@dataclass
class CallGraphEdge:
    """调用图中的一条边 (caller → callee)."""
    caller_id: str  # CallGraphNode.id
    callee_id: str  # CallGraphNode.id
    call_line: int = -1     # 调用发生的行号
    is_taint: bool = False  # 该边是否有污点数据流


@dataclass
class CallGraph:
    """
    项目调用图.

    节点: 所有方法
    边: 方法间调用关系 (可带 taint 标记)
    """
    nodes: dict[str, CallGraphNode] = field(default_factory=dict)
    # node_id → node

    edges: list[CallGraphEdge] = field(default_factory=list)
    # 所有边

    # ── 邻接表 (延迟构建) ────────────────────────────────────────
    _adj: dict[str, list[str]] = field(default_factory=dict, repr=False)
    _taint_edges: set[tuple[str, str]] = field(default_factory=set, repr=False)

    def build_adjacency(self) -> None:
        """构建邻接表和 taint 边集合."""
        self._adj.clear()
        self._taint_edges.clear()
        for edge in self.edges:
            self._adj.setdefault(edge.caller_id, []).append(edge.callee_id)
            if edge.is_taint:
                self._taint_edges.add((edge.caller_id, edge.callee_id))

    def successors(self, node_id: str) -> list[str]:
        """获取某节点的所有被调用者."""
        if not self._adj:
            self.build_adjacency()
        return self._adj.get(node_id, [])

    def has_taint(self, caller_id: str, callee_id: str) -> bool:
        """判断两节点之间是否存在 taint 数据流."""
        if not self._taint_edges and self.edges:
            self.build_adjacency()
        return (caller_id, callee_id) in self._taint_edges

    def add_node(self, node: CallGraphNode) -> None:
        self.nodes[node.id] = node

    def add_edge(self, edge: CallGraphEdge) -> None:
        self.edges.append(edge)
        # 如果已建过邻接表, 增量更新
        if self._adj:
            self._adj.setdefault(edge.caller_id, []).append(edge.callee_id)
            if edge.is_taint:
                self._taint_edges.add((edge.caller_id, edge.callee_id))


# ═══════════════════════════════════════════════════════════════════════
# 路径生成器
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class PathGenerator:
    """
    预期路径生成器.

    核心算法:
      对每个 (api_entry, log_sink) 对, 在调用图上 BFS/DFS 查找路径.
      选择最优路径 (taint > CG, 短 > 长).

    参数:
      max_path_length: 路径最大深度限制 (防止爆炸)
      max_paths_per_pair: 每对 (api, sink) 最多保留几条路径
      prefer_taint: 是否优先选 taint 路径
    """
    max_path_length: int = 20
    max_paths_per_pair: int = 1  # GONDAR 策略: 每个 sink 只选 1 条
    prefer_taint: bool = True

    def generate(
        self,
        project_name: str,
        api_entries: list[APIEntry],
        log_sinks: list[LogSink],
        call_graph: CallGraph,
    ) -> ExpectedPathSet:
        """
        生成预期路径集合.

        Args:
            project_name: 项目名
            api_entries: 所有 API 入口
            log_sinks: 所有日志打印点
            call_graph: 项目调用图

        Returns:
            ExpectedPathSet 包含所有 (api, sink) 的预期路径
        """
        call_graph.build_adjacency()

        # 构建 sink 方法 ID 集合, 用于快速判断是否到达 sink
        sink_method_map: dict[str, LogSink] = {}
        for sink in log_sinks:
            sink_id = f"{sink.class_name}.{sink.method}"
            sink_method_map[sink_id] = sink

        all_paths: list[ExpectedPath] = []

        for api in api_entries:
            entry_id = f"{api.class_name}.{api.method}"

            # 检查 entry 是否在调用图中
            if entry_id not in call_graph.nodes:
                continue

            # 从 entry 出发 BFS, 找到所有可达的 sink
            reachable_paths = self._find_paths_to_sinks(
                entry_id, sink_method_map, call_graph
            )

            # 对每个可达 sink, 选择最优路径
            for sink_method_id, raw_paths in reachable_paths.items():
                sink = sink_method_map[sink_method_id]
                selected = self._select_best_paths(
                    raw_paths, call_graph
                )

                for path_node_ids, path_source in selected:
                    # 将 node_id 序列转为 PathNode 序列
                    nodes = self._build_path_nodes(path_node_ids, call_graph)
                    ep = ExpectedPath(
                        api_entry=api,
                        log_sink=sink,
                        nodes=nodes,
                        source=path_source,
                    )
                    all_paths.append(ep)

        result = ExpectedPathSet(
            project_name=project_name,
            all_paths=all_paths,
        )
        result.build_index()
        return result

    def _find_paths_to_sinks(
        self,
        entry_id: str,
        sink_method_map: dict[str, LogSink],
        call_graph: CallGraph,
    ) -> dict[str, list[list[str]]]:
        """
        从 entry 出发 BFS, 找到所有可达 sink 的路径.

        返回: sink_method_id → [path1, path2, ...]
              其中 path 是 node_id 的有序列表
        """
        result: dict[str, list[list[str]]] = defaultdict(list)

        # BFS with path tracking
        # 状态: (current_node_id, path_so_far)
        queue: deque[tuple[str, list[str]]] = deque()
        queue.append((entry_id, [entry_id]))

        # 访问控制: 每条路径内不重复访问同一节点 (避免环)
        # 但不同路径可以经过同一节点
        # 为防止爆炸, 限制每个 sink 最多发现 max_paths_per_pair * 3 条候选路径
        sink_path_count: dict[str, int] = defaultdict(int)
        max_candidates = self.max_paths_per_pair * 3

        # 全局访问深度限制
        visited_at_depth: dict[str, int] = {}  # node_id → 最浅到达深度

        while queue:
            current, path = queue.popleft()

            # 深度限制
            if len(path) > self.max_path_length:
                continue

            # 检查是否到达了某个 sink
            if current in sink_method_map and current != entry_id:
                if sink_path_count[current] < max_candidates:
                    result[current].append(list(path))
                    sink_path_count[current] += 1
                continue  # 到达 sink 后不继续深入

            # 扩展邻居
            for neighbor in call_graph.successors(current):
                # 路径内去环
                if neighbor in path:
                    continue

                # 剪枝: 如果该节点已被更浅的路径到达, 跳过
                # (允许同深度的不同路径)
                depth = len(path)
                if neighbor in visited_at_depth:
                    if visited_at_depth[neighbor] < depth - 2:
                        # 允许 ±2 深度的探索
                        continue
                else:
                    visited_at_depth[neighbor] = depth

                queue.append((neighbor, path + [neighbor]))

        return dict(result)

    def _select_best_paths(
        self,
        raw_paths: list[list[str]],
        call_graph: CallGraph,
    ) -> list[tuple[list[str], PathSource]]:
        """
        从候选路径中选择最优的 max_paths_per_pair 条.

        排序策略 (参考 GONDAR):
          1. Taint 路径 > CG-only 路径
          2. 短路径 > 长路径
        """
        # 判断每条路径是否有 taint 边
        scored_paths: list[tuple[float, list[str], PathSource]] = []

        for path in raw_paths:
            has_taint = False
            if self.prefer_taint:
                for i in range(len(path) - 1):
                    if call_graph.has_taint(path[i], path[i + 1]):
                        has_taint = True
                        break

            source = PathSource.TAINT if has_taint else PathSource.CALL_GRAPH
            # 排序分: taint 优先 (0 < 1), 然后按长度
            priority = (0 if has_taint else 1, len(path))
            scored_paths.append((priority, path, source))

        # 排序并取 top-k
        scored_paths.sort(key=lambda x: x[0])
        selected = scored_paths[: self.max_paths_per_pair]

        return [(path, source) for _, path, source in selected]

    def _build_path_nodes(
        self,
        node_ids: list[str],
        call_graph: CallGraph,
    ) -> list[PathNode]:
        """将 node_id 序列转为 PathNode 序列."""
        result: list[PathNode] = []
        for depth, node_id in enumerate(node_ids):
            cg_node = call_graph.nodes.get(node_id)
            if cg_node:
                pn = PathNode(
                    class_name=cg_node.class_name,
                    method=cg_node.method,
                    method_signature=cg_node.method_signature,
                    src_file=cg_node.src_file,
                    line_number=cg_node.line_number,
                    depth=depth,
                )
            else:
                # fallback: 从 node_id 解析
                parts = node_id.rsplit('.', 1)
                pn = PathNode(
                    class_name=parts[0] if len(parts) == 2 else node_id,
                    method=parts[1] if len(parts) == 2 else node_id,
                    depth=depth,
                )
            result.append(pn)
        return result
