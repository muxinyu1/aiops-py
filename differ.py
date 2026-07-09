"""
differ.py — 偏差计算器

基于基本块执行流 (JaCoCo 行级覆盖) 计算两个 Trace 之间的差异.

改进算法 (受 GONDAR Reachability Progress Analysis 启发):
  1. 对齐两个 trace 的调用树 (按 content/class_namespace 匹配)
  2. 沿调用树 DFS, 找到第一个"子方法路径分歧"的节点
  3. 在分歧节点的方法内, 用行级覆盖定位具体分歧行
  4. 退化: 若无调用树信息, 按类级时序 + 行号排序
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from trace import Trace, TraceNode, CoverageData, LineCoverage
from difference import Difference, DivergencePoint


@dataclass
class TreeDivergence:
    """调用树对齐过程中发现的分歧."""
    # 分歧发生在哪个节点 (调用树上最深的公共祖先)
    ancestor_node_a: Optional[TraceNode] = None
    ancestor_node_b: Optional[TraceNode] = None
    # 分歧类型
    divergence_type: str = ""  # "child_mismatch" | "coverage_diff" | "subtree_only_in_a" | "subtree_only_in_b"
    # 调用树上的深度 (从根 = 0)
    depth: int = 0


@dataclass
class Differ:
    """
    计算两个 Trace 之间的基本块执行流偏差.

    算法 (三层定位):
      Layer 1: 调用树对齐 — DFS 对比两棵调用树, 找到第一个子节点分歧
      Layer 2: 方法内行级覆盖 — 在分歧方法内, 比较 covered_lines 定位具体行
      Layer 3: 退化兜底 — 若无调用树, 用类级时序排序 (旧逻辑)
    """

    def diff(self, trace_a: Trace, trace_b: Trace) -> Difference:
        """
        计算 trace_a 与 trace_b 在基本块执行流上的偏差.

        Args:
            trace_a: 基准 trace (通常是正常请求)
            trace_b: 目标 trace (通常是异常请求)

        Returns:
            Difference 描述两者第一次分歧的位置及详情
        """
        cov_a = trace_a.coverage
        cov_b = trace_b.coverage

        # 如果任一 trace 没有覆盖数据, 无法比较
        if cov_a is None or cov_b is None:
            return Difference(has_divergence=False)

        # ── Layer 1: 调用树对齐 ──────────────────────────────────
        tree_divergence = None
        if trace_a.nodes and trace_b.nodes:
            # 确保调用树已构建
            if not trace_a.root_nodes:
                trace_a.build_tree()
            if not trace_b.root_nodes:
                trace_b.build_tree()

            tree_divergence = self._find_tree_divergence(
                trace_a.root_nodes, trace_b.root_nodes
            )

        # ── Layer 2 & 3: 行级覆盖比较 ────────────────────────────
        map_a = self._build_class_map(cov_a)
        map_b = self._build_class_map(cov_b)

        all_classes = sorted(set(map_a.keys()) | set(map_b.keys()))

        # 收集所有分歧点
        divergences: list[DivergencePoint] = []
        classes_only_in_a: list[str] = []
        classes_only_in_b: list[str] = []
        total_divergent_lines = 0

        for class_name in all_classes:
            lc_a = map_a.get(class_name)
            lc_b = map_b.get(class_name)

            if lc_a is None:
                classes_only_in_b.append(class_name)
                if lc_b:
                    total_divergent_lines += len(lc_b.covered_lines)
                continue

            if lc_b is None:
                classes_only_in_a.append(class_name)
                total_divergent_lines += len(lc_a.covered_lines)
                continue

            # 两边都有覆盖, 比较执行的行集合
            lines_a = set(lc_a.covered_lines)
            lines_b = set(lc_b.covered_lines)

            only_in_a = sorted(lines_a - lines_b)
            only_in_b = sorted(lines_b - lines_a)

            if not only_in_a and not only_in_b:
                continue

            # 该类内第一行分歧
            all_diff_lines = sorted(only_in_a + only_in_b)
            diverge_line = all_diff_lines[0]

            common = sorted(lines_a & lines_b)
            common_prefix = [l for l in common if l < diverge_line]

            dp = DivergencePoint(
                class_name=class_name,
                src_file=lc_a.src_file,
                diverge_line=diverge_line,
                only_in_a=only_in_a,
                only_in_b=only_in_b,
                common_prefix=common_prefix,
            )
            divergences.append(dp)
            total_divergent_lines += len(only_in_a) + len(only_in_b)

        # ── 排序: 优先用调用树分歧, 否则按时序 ───────────────────
        if tree_divergence and divergences:
            divergences = self._sort_by_tree_then_time(
                divergences, tree_divergence, trace_a, trace_b
            )
        elif divergences:
            divergences = self._sort_by_execution_order(
                divergences, trace_a, trace_b
            )

        has_divergence = bool(divergences) or bool(classes_only_in_a) or bool(classes_only_in_b)

        # 确定 first_divergence
        first_divergence = divergences[0] if divergences else None
        if first_divergence is None and has_divergence:
            first_divergence = self._find_first_class_level_divergence(
                classes_only_in_a, classes_only_in_b,
                map_a, map_b, trace_a, trace_b,
            )

        return Difference(
            has_divergence=has_divergence,
            first_divergence=first_divergence,
            all_divergences=divergences,
            classes_only_in_a=classes_only_in_a,
            classes_only_in_b=classes_only_in_b,
            total_divergent_lines=total_divergent_lines,
        )

    # ═══════════════════════════════════════════════════════════════
    # Layer 1: 调用树 DFS 对齐
    # ═══════════════════════════════════════════════════════════════

    def _find_tree_divergence(
        self,
        roots_a: list[TraceNode],
        roots_b: list[TraceNode],
    ) -> Optional[TreeDivergence]:
        """
        沿调用树 DFS 对齐, 找到第一个子节点分歧.

        对齐策略: 按 (class_namespace, function) 匹配节点.
        分歧定义:
          - 一方有子调用而另一方没有
          - 同层子节点集合不一致 (一方调用了某方法而另一方没有)
          - 两方都有子节点但子节点行为不同 (递归检测)
        """
        return self._dfs_align(roots_a, roots_b, depth=0)

    def _dfs_align(
        self,
        nodes_a: list[TraceNode],
        nodes_b: list[TraceNode],
        depth: int,
    ) -> Optional[TreeDivergence]:
        """递归对齐两组同层节点, 找到最浅的分歧."""

        # 构建 (class_namespace, function) → node 的映射
        # 注意: 同一个方法可能被调用多次, 按 start_ns 排序后逐个对齐
        pairs = self._pair_nodes(nodes_a, nodes_b)

        for node_a, node_b, match_type in pairs:
            if match_type == "only_in_a":
                return TreeDivergence(
                    ancestor_node_a=node_a,
                    divergence_type="subtree_only_in_a",
                    depth=depth,
                )
            elif match_type == "only_in_b":
                return TreeDivergence(
                    ancestor_node_b=node_b,
                    divergence_type="subtree_only_in_b",
                    depth=depth,
                )
            elif match_type == "matched":
                # 两边都有这个方法调用, 递归比较子节点
                children_a = node_a.children if node_a else []
                children_b = node_b.children if node_b else []

                if children_a or children_b:
                    sub_div = self._dfs_align(children_a, children_b, depth + 1)
                    if sub_div is not None:
                        return sub_div

        return None

    def _pair_nodes(
        self,
        nodes_a: list[TraceNode],
        nodes_b: list[TraceNode],
    ) -> list[tuple[Optional[TraceNode], Optional[TraceNode], str]]:
        """
        按 (class_namespace, function) 键值对齐同层节点.

        使用贪心策略: 按执行顺序 (start_ns) 遍历, 同键的节点按顺序一一配对.
        """
        # 按 key 分组, 保持顺序
        from collections import defaultdict

        key_fn = lambda n: (n.class_namespace, n.function)

        groups_a: dict[tuple, list[TraceNode]] = defaultdict(list)
        groups_b: dict[tuple, list[TraceNode]] = defaultdict(list)

        for n in sorted(nodes_a, key=lambda n: n.start_ns):
            groups_a[key_fn(n)].append(n)
        for n in sorted(nodes_b, key=lambda n: n.start_ns):
            groups_b[key_fn(n)].append(n)

        all_keys_ordered: list[tuple] = []
        seen = set()
        for n in sorted(nodes_a + nodes_b, key=lambda n: n.start_ns):
            k = key_fn(n)
            if k not in seen:
                all_keys_ordered.append(k)
                seen.add(k)

        result: list[tuple[Optional[TraceNode], Optional[TraceNode], str]] = []

        for key in all_keys_ordered:
            list_a = groups_a.get(key, [])
            list_b = groups_b.get(key, [])

            # 逐个配对
            max_len = max(len(list_a), len(list_b))
            for i in range(max_len):
                na = list_a[i] if i < len(list_a) else None
                nb = list_b[i] if i < len(list_b) else None

                if na and nb:
                    result.append((na, nb, "matched"))
                elif na and not nb:
                    result.append((na, None, "only_in_a"))
                else:
                    result.append((None, nb, "only_in_b"))

        return result

    # ═══════════════════════════════════════════════════════════════
    # 排序: 调用树分歧优先, 时序兜底
    # ═══════════════════════════════════════════════════════════════

    def _sort_by_tree_then_time(
        self,
        divergences: list[DivergencePoint],
        tree_div: TreeDivergence,
        trace_a: Trace,
        trace_b: Trace,
    ) -> list[DivergencePoint]:
        """
        优先将调用树分歧对应的类排到最前面, 其余按时序排序.

        逻辑:
          - 调用树找到了分歧节点 (如缺失的子调用 ChildService)
          - 分歧实际发生在父节点 (调用 ChildService 的 ParentService)
          - 将父节点或分歧节点对应的类的 DivergencePoint 排在第一位
          - 其余分歧点按原有时序排序
        """
        # 提取树分歧对应的类名 (及其父节点)
        tree_div_classes = set()
        if tree_div.ancestor_node_a:
            tree_div_classes.add(tree_div.ancestor_node_a.class_namespace)
            # 如果是子节点缺失, 加上父节点
            parent = self._find_parent_node(tree_div.ancestor_node_a, trace_a)
            if parent:
                tree_div_classes.add(parent.class_namespace)
        if tree_div.ancestor_node_b:
            tree_div_classes.add(tree_div.ancestor_node_b.class_namespace)
            parent = self._find_parent_node(tree_div.ancestor_node_b, trace_b)
            if parent:
                tree_div_classes.add(parent.class_namespace)

        class_earliest_ns = self._build_class_time_map(trace_a, trace_b)

        def sort_key(dp: DivergencePoint) -> tuple[int, int, int]:
            class_dot = dp.class_name.replace('/', '.')
            # 优先级 0: 调用树分歧节点或其父节点对应的类
            is_tree_div = 0 if class_dot in tree_div_classes else 1
            # 优先级 1: 时序
            earliest = class_earliest_ns.get(class_dot, 2**63)
            # 优先级 2: 行号
            return (is_tree_div, earliest, dp.diverge_line)

        return sorted(divergences, key=sort_key)

    def _find_parent_node(
        self, node: TraceNode, trace: Trace
    ) -> Optional[TraceNode]:
        """在 trace 中查找 node 的父节点."""
        if not node.parent_span_id:
            return None
        for n in trace.nodes:
            if n.span_id == node.parent_span_id:
                return n
        return None

    def _sort_by_execution_order(
        self,
        divergences: list[DivergencePoint],
        trace_a: Trace,
        trace_b: Trace,
    ) -> list[DivergencePoint]:
        """按方法执行时序对分歧点排序 (无调用树时的退化逻辑)."""
        if not divergences:
            return divergences

        class_earliest_ns = self._build_class_time_map(trace_a, trace_b)

        def sort_key(dp: DivergencePoint) -> tuple[int, int]:
            class_dot = dp.class_name.replace('/', '.')
            earliest = class_earliest_ns.get(class_dot, float('inf'))
            return (earliest if earliest != float('inf') else 2**63, dp.diverge_line)

        return sorted(divergences, key=sort_key)

    def _build_class_time_map(
        self, trace_a: Trace, trace_b: Trace
    ) -> dict[str, int]:
        """从两个 trace 的所有 span 中, 构建 class_namespace → 最早 start_ns."""
        class_time: dict[str, int] = {}

        for nodes in (trace_a.nodes, trace_b.nodes):
            for node in nodes:
                ns = node.class_namespace
                if ns not in class_time or node.start_ns < class_time[ns]:
                    class_time[ns] = node.start_ns

        return class_time

    # ═══════════════════════════════════════════════════════════════
    # 类级分歧兜底
    # ═══════════════════════════════════════════════════════════════

    def _find_first_class_level_divergence(
        self,
        classes_only_in_a: list[str],
        classes_only_in_b: list[str],
        map_a: dict[str, LineCoverage],
        map_b: dict[str, LineCoverage],
        trace_a: Trace,
        trace_b: Trace,
    ) -> DivergencePoint | None:
        """当没有行内分歧但有类级分歧时, 按 span 时序找第一个独有类."""
        class_time = self._build_class_time_map(trace_a, trace_b)

        candidates: list[tuple[int, str, bool]] = []
        for cls in classes_only_in_a:
            cls_dot = cls.replace('/', '.')
            t = class_time.get(cls_dot, 2**63)
            candidates.append((t, cls, True))
        for cls in classes_only_in_b:
            cls_dot = cls.replace('/', '.')
            t = class_time.get(cls_dot, 2**63)
            candidates.append((t, cls, False))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[0])
        _, cls, is_in_a = candidates[0]

        if is_in_a:
            lc = map_a[cls]
            return DivergencePoint(
                class_name=cls,
                src_file=lc.src_file,
                diverge_line=min(lc.covered_lines) if lc.covered_lines else 0,
                only_in_a=sorted(lc.covered_lines),
                only_in_b=[],
            )
        else:
            lc = map_b[cls]
            return DivergencePoint(
                class_name=cls,
                src_file=lc.src_file,
                diverge_line=min(lc.covered_lines) if lc.covered_lines else 0,
                only_in_a=[],
                only_in_b=sorted(lc.covered_lines),
            )

    @staticmethod
    def _build_class_map(cov: CoverageData) -> dict[str, LineCoverage]:
        """将 CoverageData 的 class_coverages 按 class_name 索引."""
        return {lc.class_name: lc for lc in cov.class_coverages}