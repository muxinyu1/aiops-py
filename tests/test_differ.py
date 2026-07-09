"""
tests/test_differ.py — Differ 单元测试

覆盖:
  - 无覆盖数据 → 无分歧
  - 覆盖完全一致 → 无分歧
  - 行级分歧 (无调用树) → 退化时序排序
  - 调用树对齐: 子节点匹配 → 无分歧
  - 调用树对齐: 子节点缺失 → 定位到分歧方法
  - 调用树对齐: 深层递归分歧 → 找到最深分歧点
  - 类级分歧 (仅一方有覆盖)
"""

from __future__ import annotations

import pytest

from trace import Trace, TraceNode, CoverageData, LineCoverage
from difference import Difference, DivergencePoint
from differ import Differ, TreeDivergence


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

def make_trace(
    nodes: list[TraceNode] | None = None,
    class_coverages: list[LineCoverage] | None = None,
) -> Trace:
    """快速构造 Trace 用于测试."""
    cov = None
    if class_coverages is not None:
        cov = CoverageData(raw_exec=b"", class_coverages=class_coverages)
    t = Trace(
        source=None,
        sink=None,
        nodes=nodes or [],
        coverage=cov,
    )
    if nodes:
        t.build_tree()
    return t


def make_node(
    span_id: str,
    parent_span_id: str = "",
    class_namespace: str = "",
    function: str = "",
    start_ns: int = 0,
    duration_ns: int = 1000,
) -> TraceNode:
    """快速构造 TraceNode."""
    return TraceNode(
        span_id=span_id,
        parent_span_id=parent_span_id,
        trace_id="trace-1",
        content="",
        function=function,
        method_signature="",
        class_namespace=class_namespace,
        src_file=f"{class_namespace.split('.')[-1]}.java" if class_namespace else "",
        line_number=0,
        start_ns=start_ns,
        duration_ns=duration_ns,
        is_error=False,
        error_message="",
        executed_lines=[],
    )


def make_lc(
    class_name: str,
    src_file: str,
    covered_lines: list[int],
    missed_lines: list[int] | None = None,
) -> LineCoverage:
    return LineCoverage(
        class_name=class_name,
        src_file=src_file,
        covered_lines=covered_lines,
        missed_lines=missed_lines or [],
    )


# ═══════════════════════════════════════════════════════════════════
# 基本场景
# ═══════════════════════════════════════════════════════════════════

class TestDifferBasic:
    """无调用树时的基本分歧检测."""

    def test_no_coverage_data(self):
        """任一 trace 无覆盖 → 不分歧."""
        d = Differ()
        t_a = make_trace(class_coverages=[make_lc("A", "A.java", [1, 2, 3])])
        t_b = make_trace(class_coverages=None)
        result = d.diff(t_a, t_b)
        assert result.has_divergence is False

    def test_identical_coverage(self):
        """覆盖完全一致 → 不分歧."""
        d = Differ()
        lc = [make_lc("com/example/Svc", "Svc.java", [10, 20, 30])]
        t_a = make_trace(class_coverages=lc)
        t_b = make_trace(class_coverages=lc)
        result = d.diff(t_a, t_b)
        assert result.has_divergence is False
        assert result.first_divergence is None

    def test_line_divergence_no_spans(self):
        """有行级分歧但无 span → 退化到类名排序."""
        d = Differ()
        t_a = make_trace(class_coverages=[
            make_lc("com/example/Svc", "Svc.java", [10, 20, 30]),
        ])
        t_b = make_trace(class_coverages=[
            make_lc("com/example/Svc", "Svc.java", [10, 20, 40]),
        ])
        result = d.diff(t_a, t_b)
        assert result.has_divergence is True
        assert result.first_divergence is not None
        assert result.first_divergence.class_name == "com/example/Svc"
        assert result.first_divergence.diverge_line == 30  # first diff line
        assert 30 in result.first_divergence.only_in_a
        assert 40 in result.first_divergence.only_in_b

    def test_class_only_in_one_side(self):
        """一方有某类覆盖另一方没有."""
        d = Differ()
        t_a = make_trace(class_coverages=[
            make_lc("com/example/A", "A.java", [1, 2]),
            make_lc("com/example/B", "B.java", [5, 6]),
        ])
        t_b = make_trace(class_coverages=[
            make_lc("com/example/A", "A.java", [1, 2]),
        ])
        result = d.diff(t_a, t_b)
        assert result.has_divergence is True
        assert "com/example/B" in result.classes_only_in_a

    def test_common_prefix(self):
        """分歧前的共同前缀正确计算."""
        d = Differ()
        t_a = make_trace(class_coverages=[
            make_lc("com/X", "X.java", [1, 2, 3, 10, 11]),
        ])
        t_b = make_trace(class_coverages=[
            make_lc("com/X", "X.java", [1, 2, 3, 20, 21]),
        ])
        result = d.diff(t_a, t_b)
        fp = result.first_divergence
        assert fp is not None
        assert fp.common_prefix == [1, 2, 3]
        assert fp.diverge_line == 10


# ═══════════════════════════════════════════════════════════════════
# 时序排序 (无调用树但有 span)
# ═══════════════════════════════════════════════════════════════════

class TestDifferTimeOrdering:
    """有 span 但调用树对齐后无分歧时, 按时序排序."""

    def test_sort_by_span_time(self):
        """多个类分歧 → 按 span start_ns 排序."""
        d = Differ()

        nodes = [
            make_node("s1", class_namespace="com.example.Late", function="late", start_ns=2000),
            make_node("s2", class_namespace="com.example.Early", function="early", start_ns=1000),
        ]

        t_a = make_trace(
            nodes=nodes,
            class_coverages=[
                make_lc("com/example/Late", "Late.java", [1, 2]),
                make_lc("com/example/Early", "Early.java", [10, 20]),
            ],
        )
        t_b = make_trace(
            nodes=nodes,
            class_coverages=[
                make_lc("com/example/Late", "Late.java", [1, 3]),
                make_lc("com/example/Early", "Early.java", [10, 30]),
            ],
        )

        result = d.diff(t_a, t_b)
        assert len(result.all_divergences) == 2
        # Early (start_ns=1000) 排在 Late (start_ns=2000) 前面
        assert result.all_divergences[0].class_name == "com/example/Early"
        assert result.all_divergences[1].class_name == "com/example/Late"


# ═══════════════════════════════════════════════════════════════════
# 调用树 DFS 对齐
# ═══════════════════════════════════════════════════════════════════

class TestDifferTreeAlignment:
    """调用树 DFS 对齐的分歧检测."""

    def test_tree_aligned_no_divergence(self):
        """调用树完全一致且覆盖一致 → 无分歧."""
        d = Differ()

        # 调用链: Controller → Service → Dao
        nodes_a = [
            make_node("s1", "", "com.example.Controller", "handle", 1000),
            make_node("s2", "s1", "com.example.Service", "process", 2000),
            make_node("s3", "s2", "com.example.Dao", "query", 3000),
        ]
        nodes_b = [
            make_node("s1", "", "com.example.Controller", "handle", 1000),
            make_node("s2", "s1", "com.example.Service", "process", 2000),
            make_node("s3", "s2", "com.example.Dao", "query", 3000),
        ]

        lc = [
            make_lc("com/example/Controller", "Controller.java", [10, 20]),
            make_lc("com/example/Service", "Service.java", [5, 6, 7]),
            make_lc("com/example/Dao", "Dao.java", [1, 2]),
        ]

        t_a = make_trace(nodes=nodes_a, class_coverages=lc)
        t_b = make_trace(nodes=nodes_b, class_coverages=lc)
        result = d.diff(t_a, t_b)
        assert result.has_divergence is False

    def test_tree_child_missing_in_b(self):
        """trace_b 缺少一个子调用 → 树对齐找到分歧."""
        d = Differ()

        # A: Controller → Service → Dao
        nodes_a = [
            make_node("s1", "", "com.example.Controller", "handle", 1000),
            make_node("s2", "s1", "com.example.Service", "process", 2000),
            make_node("s3", "s2", "com.example.Dao", "query", 3000),
        ]
        # B: Controller → Service (没有 Dao 调用)
        nodes_b = [
            make_node("s1", "", "com.example.Controller", "handle", 1000),
            make_node("s2", "s1", "com.example.Service", "process", 2000),
        ]

        t_a = make_trace(
            nodes=nodes_a,
            class_coverages=[
                make_lc("com/example/Controller", "Controller.java", [10, 20]),
                make_lc("com/example/Service", "Service.java", [5, 6, 7]),
                make_lc("com/example/Dao", "Dao.java", [1, 2]),
            ],
        )
        t_b = make_trace(
            nodes=nodes_b,
            class_coverages=[
                make_lc("com/example/Controller", "Controller.java", [10, 20]),
                make_lc("com/example/Service", "Service.java", [5, 6]),
            ],
        )

        result = d.diff(t_a, t_b)
        assert result.has_divergence is True
        # Service 是分歧发生的位置 (它的子调用 Dao 在 B 中缺失)
        # 树对齐应该把 Service 或 Dao 级别的分歧排到前面
        # first_divergence 应该是 Service (行7只在A) 或 Dao (类级分歧)
        fp = result.first_divergence
        assert fp is not None

    def test_tree_deep_divergence(self):
        """深层递归分歧: 第二层子调用不同."""
        d = Differ()

        # A: Root → A → A1, A2
        # B: Root → A → A1 (没有 A2)
        nodes_a = [
            make_node("r", "", "com.Root", "entry", 1000),
            make_node("a", "r", "com.A", "doA", 2000),
            make_node("a1", "a", "com.A1", "step1", 3000),
            make_node("a2", "a", "com.A2", "step2", 4000),
        ]
        nodes_b = [
            make_node("r", "", "com.Root", "entry", 1000),
            make_node("a", "r", "com.A", "doA", 2000),
            make_node("a1", "a", "com.A1", "step1", 3000),
        ]

        t_a = make_trace(
            nodes=nodes_a,
            class_coverages=[
                make_lc("com/Root", "Root.java", [1, 2]),
                make_lc("com/A", "A.java", [10, 20, 30]),
                make_lc("com/A1", "A1.java", [5]),
                make_lc("com/A2", "A2.java", [7, 8]),
            ],
        )
        t_b = make_trace(
            nodes=nodes_b,
            class_coverages=[
                make_lc("com/Root", "Root.java", [1, 2]),
                make_lc("com/A", "A.java", [10, 20]),
                make_lc("com/A1", "A1.java", [5]),
            ],
        )

        result = d.diff(t_a, t_b)
        assert result.has_divergence is True
        # A2 仅在 A 中有 → classes_only_in_a
        assert "com/A2" in result.classes_only_in_a
        # A 行级分歧 (行30 only in A)
        a_divs = [dp for dp in result.all_divergences if dp.class_name == "com/A"]
        assert len(a_divs) == 1
        assert 30 in a_divs[0].only_in_a

    def test_tree_divergence_prioritized_over_time(self):
        """调用树分歧应优先于纯时序排序."""
        d = Differ()

        # 两个类都有分歧, 但 ClassEarly (时序早) 和 ClassLate (时序晚, 但在调用树分歧路径上)
        # 调用树: Root → ClassLate (trace_b 中缺少 ClassLate 的子调用)
        nodes_a = [
            make_node("r", "", "com.Root", "entry", 1000),
            make_node("e", "r", "com.ClassEarly", "early", 1500),
            make_node("l", "r", "com.ClassLate", "late", 2000),
            make_node("lc", "l", "com.ClassLateChild", "child", 3000),
        ]
        nodes_b = [
            make_node("r", "", "com.Root", "entry", 1000),
            make_node("e", "r", "com.ClassEarly", "early", 1500),
            make_node("l", "r", "com.ClassLate", "late", 2000),
            # ClassLateChild 缺失
        ]

        t_a = make_trace(
            nodes=nodes_a,
            class_coverages=[
                make_lc("com/Root", "Root.java", [1]),
                make_lc("com/ClassEarly", "ClassEarly.java", [10, 20]),
                make_lc("com/ClassLate", "ClassLate.java", [5, 6, 7]),
                make_lc("com/ClassLateChild", "ClassLateChild.java", [1, 2]),
            ],
        )
        t_b = make_trace(
            nodes=nodes_b,
            class_coverages=[
                make_lc("com/Root", "Root.java", [1]),
                make_lc("com/ClassEarly", "ClassEarly.java", [10, 30]),  # 行级分歧
                make_lc("com/ClassLate", "ClassLate.java", [5, 6]),     # 行级分歧
            ],
        )

        result = d.diff(t_a, t_b)
        assert result.has_divergence is True
        # 树分歧在 ClassLate 层 (缺少 ClassLateChild 子调用)
        # first_divergence 应该是 ClassLate 而不是 ClassEarly (时序上更早)
        fp = result.first_divergence
        assert fp is not None
        assert fp.class_name == "com/ClassLate"


# ═══════════════════════════════════════════════════════════════════
# _pair_nodes 配对逻辑
# ═══════════════════════════════════════════════════════════════════

class TestPairNodes:
    """_pair_nodes 的直接测试."""

    def test_exact_match(self):
        d = Differ()
        n1 = make_node("a", class_namespace="com.X", function="foo", start_ns=100)
        n2 = make_node("b", class_namespace="com.X", function="foo", start_ns=200)
        pairs = d._pair_nodes([n1], [n2])
        assert len(pairs) == 1
        assert pairs[0][2] == "matched"

    def test_only_in_a(self):
        d = Differ()
        n1 = make_node("a", class_namespace="com.X", function="foo", start_ns=100)
        n2 = make_node("b", class_namespace="com.Y", function="bar", start_ns=200)
        pairs = d._pair_nodes([n1], [n2])
        only_a = [p for p in pairs if p[2] == "only_in_a"]
        only_b = [p for p in pairs if p[2] == "only_in_b"]
        assert len(only_a) == 1
        assert len(only_b) == 1

    def test_multiple_calls_same_method(self):
        """同一方法调用多次 → 按顺序配对."""
        d = Differ()
        na1 = make_node("a1", class_namespace="com.X", function="foo", start_ns=100)
        na2 = make_node("a2", class_namespace="com.X", function="foo", start_ns=300)
        nb1 = make_node("b1", class_namespace="com.X", function="foo", start_ns=150)
        nb2 = make_node("b2", class_namespace="com.X", function="foo", start_ns=350)

        pairs = d._pair_nodes([na1, na2], [nb1, nb2])
        matched = [p for p in pairs if p[2] == "matched"]
        assert len(matched) == 2


# ═══════════════════════════════════════════════════════════════════
# Difference 数据类
# ═══════════════════════════════════════════════════════════════════

class TestDifference:
    """Difference 数据类的属性和方法."""

    def test_divergence_summary_no_divergence(self):
        diff = Difference(has_divergence=False)
        assert "No divergence" in diff.divergence_summary

    def test_divergence_summary_with_point(self):
        dp = DivergencePoint(
            class_name="com/example/Svc",
            src_file="Svc.java",
            diverge_line=42,
            only_in_a=[42, 43],
            only_in_b=[50],
        )
        diff = Difference(
            has_divergence=True,
            first_divergence=dp,
            all_divergences=[dp],
            total_divergent_lines=3,
        )
        assert "Svc.java:42" in diff.divergence_summary
