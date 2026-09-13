"""
path_differ.py — Trace vs ExpectedPath 偏差计算

对比一个实际 Trace (动态执行) 与一条 ExpectedPath (静态预期),
定位执行流在哪个节点首次偏离预期路径.

核心算法 (参考 GONDAR Reachability Progress Analysis):
  1. 沿预期路径 DFS 对齐实际 trace 的调用树
  2. 找到实际 trace 中最深到达的预期节点 (deepest matched node)
  3. 记录预期路径上从该节点之后第一个未到达的节点 (first missed node)
  4. 返回偏差描述: 到达深度, 未到达节点, 偏离原因
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from trace import Trace, TraceNode
from expected_path import ExpectedPath, PathNode


# ═══════════════════════════════════════════════════════════════════════
# 偏差数据模型
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class PathDivergence:
    """
    实际 Trace 与预期 ExpectedPath 的偏差描述.

    语义: "实际执行到达了预期路径的第 X 层, 但未能到达第 X+1 层的预期节点 Y"
    """
    # ── 基本信息 ─────────────────────────────────────────────────
    expected_path: ExpectedPath
    # 比较的预期路径

    has_divergence: bool = False
    # 是否存在偏差 (False 表示完全匹配预期路径)

    # ── 到达进度 ─────────────────────────────────────────────────
    reached_depth: int = -1
    # 实际 trace 沿预期路径到达的最深层数 (0-based)
    # -1 表示连第 0 层 (entry) 都没有到达

    reached_node: Optional[PathNode] = None
    # 预期路径上最后一个匹配的节点

    matched_trace_node: Optional[TraceNode] = None
    # 实际 trace 中与 reached_node 匹配的 TraceNode

    # ── 偏离点 ───────────────────────────────────────────────────
    first_missed_depth: int = -1
    # 预期路径上第一个未到达的节点深度

    first_missed_node: Optional[PathNode] = None
    # 预期路径上第一个未到达的节点 (应该走但实际没走)

    # ── 原因分类 ─────────────────────────────────────────────────
    divergence_reason: str = ""
    # "not_started" — 实际 trace 根本没有匹配预期 entry
    # "partial_reach" — 到达了部分预期路径但未走完
    # "full_reach" — 完全到达预期路径 (has_divergence=False)
    # "wrong_path" — 实际 trace 走了预期路径外的方法

    # ── 额外上下文 ───────────────────────────────────────────────
    actual_path_sequence: list[str] = field(default_factory=list)
    # 实际 trace 的方法调用序列 (class.method 格式)

    response_body: str = ""
    # HTTP 响应 body (截断). not_started 时常含参数校验错误详情,
    # 如 "收货地址不能为空", 对引导 LLM 构造合法请求至关重要

    @property
    def reach_rate(self) -> float:
        """到达率: 到达深度 / 预期路径长度."""
        if self.expected_path.path_length == 0:
            return 1.0
        return (self.reached_depth + 1) / self.expected_path.path_length

    @property
    def summary(self) -> str:
        """人类可读的偏差摘要."""
        if not self.has_divergence:
            return f"✓ Full match: reached all {self.expected_path.path_length} nodes"

        ep_len = self.expected_path.path_length
        if self.reached_depth == -1:
            return (
                f"✗ Not started: expected entry {self.expected_path.nodes[0].qualified_name} "
                f"not found in trace"
            )

        if self.first_missed_node:
            return (
                f"✗ Divergence at depth {self.first_missed_depth}/{ep_len}: "
                f"reached {self.reached_node.qualified_name if self.reached_node else '?'}, "
                f"missed {self.first_missed_node.qualified_name}"
            )

        return f"✗ Partial reach: {self.reached_depth + 1}/{ep_len} nodes"


# ═══════════════════════════════════════════════════════════════════════
# 偏差计算器
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class PathDiffer:
    """
    Trace vs ExpectedPath 偏差计算器.

    算法:
      1. 将实际 Trace 构建为调用树
      2. 沿预期路径 DFS, 在实际 trace 的调用树上查找匹配节点
      3. 记录最深匹配深度, 定位第一个未匹配节点
      4. 返回 PathDivergence
    """

    def diff(self, trace: Trace, expected_path: ExpectedPath) -> PathDivergence:
        """
        计算实际 Trace 与预期 ExpectedPath 的偏差.

        Args:
            trace: 实际执行的 trace (动态)
            expected_path: 预期执行路径 (静态)

        Returns:
            PathDivergence 描述偏差详情
        """
        # 确保 trace 的调用树已构建
        if not trace.root_nodes:
            trace.build_tree()

        # 提取响应 body (execute_with_trace 动态附加在 trace 上, 含校验错误详情)
        resp_body = getattr(trace, "response_body", "") or ""
        resp_body = resp_body[:500]  # 截断防爆

        # 提取实际调用序列 (用于诊断)
        actual_sequence = [
            f"{node.class_namespace.replace('/', '.')}.{node.function}"
            for node in trace.nodes
        ]

        # 空预期路径 → 无法比较
        if not expected_path.nodes:
            return PathDivergence(
                expected_path=expected_path,
                has_divergence=True,
                divergence_reason="empty_expected_path",
                actual_path_sequence=actual_sequence,
                response_body=resp_body,
            )

        # 开始 DFS 对齐
        alignment = self._align_path(trace, expected_path)

        # 构建 PathDivergence
        reached_depth = alignment["reached_depth"]
        reached_node = alignment["reached_node"]
        matched_trace_node = alignment["matched_trace_node"]

        # 判断是否完全匹配
        full_match = (
            reached_depth == len(expected_path.nodes) - 1
        )

        if full_match:
            return PathDivergence(
                expected_path=expected_path,
                has_divergence=False,
                reached_depth=reached_depth,
                reached_node=reached_node,
                matched_trace_node=matched_trace_node,
                divergence_reason="full_reach",
                actual_path_sequence=actual_sequence,
                response_body=resp_body,
            )

        # 有偏差: 定位第一个未到达节点
        first_missed_depth = reached_depth + 1
        first_missed_node = expected_path.node_at_depth(first_missed_depth)

        # 判断偏离原因
        if reached_depth == -1:
            reason = "not_started"
        else:
            reason = "partial_reach"

        return PathDivergence(
            expected_path=expected_path,
            has_divergence=True,
            reached_depth=reached_depth,
            reached_node=reached_node,
            matched_trace_node=matched_trace_node,
            first_missed_depth=first_missed_depth,
            first_missed_node=first_missed_node,
            divergence_reason=reason,
            actual_path_sequence=actual_sequence,
            response_body=resp_body,
        )

    def _align_path(
        self, trace: Trace, expected_path: ExpectedPath
    ) -> dict:
        """
        沿预期路径对齐实际 trace, 找到最深匹配节点.

        策略:
          1. 优先用树结构对齐 (parent→child 关系)
          2. 若树对齐不完整, 退化为平铺匹配 (在全部 nodes 中查找)
          3. 取两种策略中更深的结果

        返回:
          {
            "reached_depth": int,  # 最深到达的预期节点深度
            "reached_node": PathNode,
            "matched_trace_node": TraceNode,
          }
        """
        # 策略 1: 树对齐
        tree_result = self._align_by_tree(trace, expected_path)

        # 策略 2: 平铺匹配 (处理 parent_span_id 丢失的情况)
        flat_result = self._align_by_flat(trace, expected_path)

        # 取更深的结果
        if flat_result["reached_depth"] > tree_result["reached_depth"]:
            return flat_result
        return tree_result

    def _align_by_tree(
        self, trace: Trace, expected_path: ExpectedPath
    ) -> dict:
        """树结构对齐: 沿 parent→child 逐层匹配."""
        reached_depth = -1
        reached_node = None
        matched_trace_node = None

        current_trace_nodes = trace.root_nodes

        for depth, expected_node in enumerate(expected_path.nodes):
            matched = self._find_matching_node(
                expected_node, current_trace_nodes
            )

            if matched is None:
                break

            reached_depth = depth
            reached_node = expected_node
            matched_trace_node = matched

            # 下一层: 从 matched 的 children 中继续查找
            if depth < len(expected_path.nodes) - 1:
                current_trace_nodes = matched.children
            else:
                break

        return {
            "reached_depth": reached_depth,
            "reached_node": reached_node,
            "matched_trace_node": matched_trace_node,
        }

    def _align_by_flat(
        self, trace: Trace, expected_path: ExpectedPath
    ) -> dict:
        """
        平铺匹配: 在全部 trace nodes 中查找预期路径的每个节点.

        处理 parent_span_id 缺失导致树结构不完整的情况.
        不要求父子关系, 只要求预期路径上的方法都出现在 trace 中.
        """
        reached_depth = -1
        reached_node = None
        matched_trace_node = None

        all_trace_nodes = trace.nodes

        for depth, expected_node in enumerate(expected_path.nodes):
            matched = self._find_matching_node(
                expected_node, all_trace_nodes
            )

            if matched is None:
                break

            reached_depth = depth
            reached_node = expected_node
            matched_trace_node = matched

        return {
            "reached_depth": reached_depth,
            "reached_node": reached_node,
            "matched_trace_node": matched_trace_node,
        }

    def _find_matching_node(
        self, expected_node: PathNode, trace_nodes: list[TraceNode]
    ) -> Optional[TraceNode]:
        """
        在一组 TraceNode 中查找与 expected_node 匹配的节点.

        匹配策略: class_name 和 method 都相等.
        """
        for tn in trace_nodes:
            if expected_node.matches_trace_node(
                tn.class_namespace, tn.function
            ):
                return tn
        return None


# ═══════════════════════════════════════════════════════════════════════
# 批量比较工具
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class PathDifferBatch:
    """
    批量比较: 一个 Trace 与多条 ExpectedPath.

    场景: fuzz 发请求拿到 trace, 需要和该 API 对应的所有预期路径做比较,
         找到最匹配的路径 (reach_rate 最高).
    """

    def diff_all(
        self, trace: Trace, expected_paths: list[ExpectedPath]
    ) -> list[PathDivergence]:
        """
        将 trace 与每条 expected_path 比较, 返回所有偏差.

        结果按 reach_rate 降序排列 (最匹配的在前).
        """
        differ = PathDiffer()
        divergences: list[PathDivergence] = []

        for ep in expected_paths:
            div = differ.diff(trace, ep)
            divergences.append(div)

        # 按到达率降序排列
        divergences.sort(key=lambda d: d.reach_rate, reverse=True)
        return divergences

    def best_match(
        self, trace: Trace, expected_paths: list[ExpectedPath]
    ) -> Optional[PathDivergence]:
        """返回最佳匹配 (reach_rate 最高的路径)."""
        if not expected_paths:
            return None
        divergences = self.diff_all(trace, expected_paths)
        return divergences[0] if divergences else None
