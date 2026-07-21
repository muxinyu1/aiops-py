"""
snapshot_executor.py — 两阶段偏差计算: 先定位偏差, 再采集运行时变量快照

工作流程:
  Phase 1: 正常执行 → 得到偏差 (DivergencePoint: 类名、方法名、行号)
  Phase 2: 带 X-Snapshot-Methods 头重新执行 → 采集偏差方法处的变量快照
  最终: 将快照数据合并到 DivergencePoint 中

设计原则:
  - 第一阶段复用现有 differ 逻辑, 不修改原有流程
  - 第二阶段利用 trace-agent 的 SnapshotTargetRegistry 按需采集
  - 通过 HTTP 请求头 X-Snapshot-Methods 传递需要快照的方法列表
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from difference import Difference, DivergencePoint, VariableSnapshot
from trace import Trace, TraceNode


@dataclass
class SnapshotConfig:
    """快照采集配置."""

    # 快照方法的上下文范围: 不仅采集分歧方法本身, 还采集其 N 层调用栈
    context_depth: int = 2
    # 如果为 True, 对所有方法采集快照 (等价于 X-Snapshot-Methods: *)
    capture_all: bool = False
    # 最大快照方法数 (避免 header 过长)
    max_snapshot_methods: int = 20


@dataclass
class SnapshotDiffer:
    """
    两阶段偏差计算器.

    使用方式:
        sd = SnapshotDiffer(config=SnapshotConfig())

        # Phase 1: 计算偏差 (复用现有 Differ)
        difference = differ.diff(trace_a, trace_b)

        # Phase 2: 根据偏差结果, 构造快照请求头, 重新执行获取变量快照
        snapshot_methods = sd.build_snapshot_targets(difference, trace_a)
        # → 用这些 snapshot_methods 作为 X-Snapshot-Methods 头重新请求
        # → 从新 trace 中提取快照数据
        enriched_diff = sd.enrich_difference(difference, trace_a_with_snapshot, trace_b_with_snapshot)
    """

    config: SnapshotConfig = field(default_factory=SnapshotConfig)

    def build_snapshot_targets(
        self,
        difference: Difference,
        trace_a: Optional[Trace] = None,
        trace_b: Optional[Trace] = None,
    ) -> str:
        """
        根据偏差计算结果, 构建需要快照采集的方法列表.

        返回逗号分隔的方法标识字符串, 用作 X-Snapshot-Methods 请求头值.
        格式: "com.example.Service.validate,com.example.Dao.insert"

        策略:
          1. 分歧方法本身 (DivergencePoint.class_name + 匹配的方法)
          2. 分歧方法的调用者 (往上 context_depth 层)
          3. classes_only_in_a / classes_only_in_b 中的入口方法
        """
        if self.config.capture_all:
            return "*"

        targets: set[str] = set()

        # 从 DivergencePoint 中提取分歧所在的类
        for dp in difference.all_divergences[:self.config.max_snapshot_methods]:
            # 类名格式 "com/example/service/UserService" → "com.example.service.UserService"
            class_name = dp.class_name.replace("/", ".")
            targets.add(class_name)

            # 从 trace 中找到该类内的具体方法 (行号匹配)
            methods_in_class = self._find_methods_at_divergence(
                dp, trace_a, trace_b
            )
            targets.update(methods_in_class)

        # 添加调用上下文 (分歧方法的 caller 链)
        if trace_a and trace_a.nodes:
            context_methods = self._find_caller_context(
                difference, trace_a, self.config.context_depth
            )
            targets.update(context_methods)

        if trace_b and trace_b.nodes:
            context_methods = self._find_caller_context(
                difference, trace_b, self.config.context_depth
            )
            targets.update(context_methods)

        # 仅在一方存在的类, 加入其入口方法
        for cls in difference.classes_only_in_a[:5]:
            targets.add(cls.replace("/", "."))
        for cls in difference.classes_only_in_b[:5]:
            targets.add(cls.replace("/", "."))

        # 截断
        target_list = sorted(targets)[:self.config.max_snapshot_methods]
        return ",".join(target_list)

    def enrich_difference(
        self,
        difference: Difference,
        trace_a_snapshot: Trace,
        trace_b_snapshot: Trace,
    ) -> Difference:
        """
        用快照 trace 中的变量数据丰富偏差结果.

        将 trace 中 TraceNode 的 args_snapshot / return_snapshot / this_snapshot
        匹配到 DivergencePoint, 生成 VariableSnapshot.
        """
        # 建立 class.method → TraceNode 的索引 (保留所有匹配节点)
        index_a = self._build_node_index(trace_a_snapshot)
        index_b = self._build_node_index(trace_b_snapshot)

        for dp in difference.all_divergences:
            class_name = dp.class_name.replace("/", ".")

            # 从 trace_a 中找到分歧类相关的带快照节点
            snapshot_a = self._extract_snapshot_for_divergence(dp, index_a)
            if snapshot_a and snapshot_a.has_data:
                dp.snapshot_a = snapshot_a

            # 从 trace_b 中找到分歧类相关的带快照节点
            snapshot_b = self._extract_snapshot_for_divergence(dp, index_b)
            if snapshot_b and snapshot_b.has_data:
                dp.snapshot_b = snapshot_b

        return difference

    def build_snapshot_header(self, difference: Difference,
                              trace_a: Optional[Trace] = None,
                              trace_b: Optional[Trace] = None) -> dict[str, str]:
        """
        构建完整的快照请求头字典 (方便直接传给 requests/urllib).

        Returns:
            {"X-Return-Trace": "true", "X-Snapshot-Methods": "com.example.Service.validate,..."}
        """
        methods = self.build_snapshot_targets(difference, trace_a, trace_b)
        headers = {"X-Return-Trace": "true"}
        if methods:
            headers["X-Snapshot-Methods"] = methods
        return headers

    # ═══════════════════════════════════════════════════════════════
    # 内部方法
    # ═══════════════════════════════════════════════════════════════

    def _find_methods_at_divergence(
        self,
        dp: DivergencePoint,
        trace_a: Optional[Trace],
        trace_b: Optional[Trace],
    ) -> list[str]:
        """找到分歧行号附近的具体方法 (从 trace 节点的行号匹配)."""
        results = []
        class_name = dp.class_name.replace("/", ".")

        for trace in [trace_a, trace_b]:
            if trace is None:
                continue
            for node in trace.nodes:
                if node.class_namespace == class_name:
                    # 如果该节点的行号在分歧区域内
                    if dp.diverge_line > 0 and node.line_number > 0:
                        # 方法起始行在分歧前后 50 行范围内, 视为相关
                        if abs(node.line_number - dp.diverge_line) < 50:
                            results.append(f"{class_name}.{node.function}")

        return results

    def _find_caller_context(
        self,
        difference: Difference,
        trace: Trace,
        depth: int,
    ) -> list[str]:
        """沿调用树往上找 caller, 作为快照的上下文方法."""
        if not trace.root_nodes:
            trace.build_tree()

        # 建立 span_id → node 索引
        node_map: dict[str, TraceNode] = {n.span_id: n for n in trace.nodes}
        # 建立 child → parent 映射
        parent_map: dict[str, str] = {}
        for node in trace.nodes:
            if node.parent_span_id:
                parent_map[node.span_id] = node.parent_span_id

        context_methods: list[str] = []
        divergence_classes = set()
        for dp in difference.all_divergences:
            divergence_classes.add(dp.class_name.replace("/", "."))

        # 找到分歧类的节点, 然后往上追溯 caller
        for node in trace.nodes:
            if node.class_namespace in divergence_classes:
                current_id = node.span_id
                for _ in range(depth):
                    parent_id = parent_map.get(current_id)
                    if not parent_id or parent_id not in node_map:
                        break
                    parent_node = node_map[parent_id]
                    context_methods.append(
                        f"{parent_node.class_namespace}.{parent_node.function}"
                    )
                    current_id = parent_id

        return context_methods

    def _build_node_index(
        self, trace: Trace
    ) -> dict[str, list[TraceNode]]:
        """建立 class_namespace → [TraceNode] 索引, 只保留有快照的节点."""
        index: dict[str, list[TraceNode]] = {}
        for node in trace.nodes:
            has_snapshot = (
                node.args_snapshot is not None
                or node.return_snapshot is not None
                or node.this_snapshot is not None
            )
            if has_snapshot:
                key = node.class_namespace
                if key not in index:
                    index[key] = []
                index[key].append(node)
        return index

    def _extract_snapshot_for_divergence(
        self,
        dp: DivergencePoint,
        node_index: dict[str, list[TraceNode]],
    ) -> Optional[VariableSnapshot]:
        """从节点索引中提取与分歧点匹配的变量快照."""
        class_name = dp.class_name.replace("/", ".")
        nodes = node_index.get(class_name, [])

        if not nodes:
            return None

        # 按行号距离排序, 找最接近分歧行的节点
        def line_distance(node: TraceNode) -> int:
            if node.line_number <= 0 or dp.diverge_line <= 0:
                return 9999
            return abs(node.line_number - dp.diverge_line)

        nodes_sorted = sorted(nodes, key=line_distance)
        best_node = nodes_sorted[0]

        # 合并该类所有有快照的节点 (如果多个方法都在分歧区域内)
        # 取距离分歧行最近的那个节点的快照
        snapshot = VariableSnapshot(
            args=best_node.args_snapshot,
            return_value=best_node.return_snapshot,
            this_state=best_node.this_snapshot,
        )
        return snapshot
