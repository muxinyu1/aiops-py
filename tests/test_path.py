"""
tests/test_path.py — 预期路径生成与偏差计算的单元测试

覆盖:
  - ExpectedPath 数据模型
  - PathGenerator 路径生成
  - PathDiffer 偏差计算 (full match, partial, not started)
  - PathDifferBatch 批量比较
"""

from __future__ import annotations

import pytest

from expected_path import (
    APIEntry,
    LogSink,
    PathNode,
    ExpectedPath,
    ExpectedPathSet,
    PathSource,
)
from path_generator import (
    CallGraph,
    CallGraphNode,
    CallGraphEdge,
    PathGenerator,
)
from path_differ import PathDiffer, PathDivergence, PathDifferBatch
from trace import Trace, TraceNode


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def make_api(class_name: str, method: str, http_path: str = "") -> APIEntry:
    return APIEntry(
        class_name=class_name,
        method=method,
        http_method="GET",
        http_path=http_path,
    )


def make_sink(class_name: str, method: str, line: int = 10) -> LogSink:
    return LogSink(
        class_name=class_name,
        method=method,
        line_number=line,
        log_level="ERROR",
        log_api="log.error",
    )


def make_trace_node(
    span_id: str,
    parent_span_id: str = "",
    class_namespace: str = "",
    function: str = "",
    start_ns: int = 0,
) -> TraceNode:
    return TraceNode(
        span_id=span_id,
        parent_span_id=parent_span_id,
        trace_id="t1",
        content=f"{class_namespace}.{function}",
        function=function,
        method_signature=f"{function}()",
        class_namespace=class_namespace,
        src_file=f"{class_namespace.split('.')[-1]}.java",
        line_number=1,
        start_ns=start_ns,
        duration_ns=1000,
    )


def make_trace(nodes: list[TraceNode]) -> Trace:
    t = Trace(source=None, sink=None, nodes=nodes)
    t.build_tree()
    return t


def make_expected_path(
    api: APIEntry,
    sink: LogSink,
    methods: list[tuple[str, str]],  # (class_name, method) pairs
    source: PathSource = PathSource.CALL_GRAPH,
) -> ExpectedPath:
    nodes = [
        PathNode(class_name=cn, method=m, depth=i)
        for i, (cn, m) in enumerate(methods)
    ]
    return ExpectedPath(
        api_entry=api,
        log_sink=sink,
        nodes=nodes,
        source=source,
    )


# ═══════════════════════════════════════════════════════════════════════
# ExpectedPath 数据模型
# ═══════════════════════════════════════════════════════════════════════

class TestExpectedPathModel:

    def test_path_length(self):
        api = make_api("com.A", "handle")
        sink = make_sink("com.C", "logError")
        ep = make_expected_path(api, sink, [
            ("com.A", "handle"),
            ("com.B", "process"),
            ("com.C", "logError"),
        ])
        assert ep.path_length == 3

    def test_method_sequence(self):
        api = make_api("com.A", "handle")
        sink = make_sink("com.C", "logError")
        ep = make_expected_path(api, sink, [
            ("com.A", "handle"),
            ("com.B", "process"),
            ("com.C", "logError"),
        ])
        assert ep.method_sequence == [
            "com.A.handle",
            "com.B.process",
            "com.C.logError",
        ]

    def test_confidence_taint(self):
        api = make_api("com.A", "handle")
        sink = make_sink("com.C", "log")
        ep = ExpectedPath(
            api_entry=api,
            log_sink=sink,
            nodes=[PathNode(class_name="com.A", method="handle")],
            source=PathSource.TAINT,
        )
        assert ep.confidence == 0.8

    def test_path_set_index(self):
        api1 = make_api("com.A", "handle", "/api/a")
        api2 = make_api("com.B", "list", "/api/b")
        sink = make_sink("com.Log", "error")

        ep1 = make_expected_path(api1, sink, [("com.A", "handle"), ("com.Log", "error")])
        ep2 = make_expected_path(api2, sink, [("com.B", "list"), ("com.Log", "error")])

        ps = ExpectedPathSet(project_name="test", all_paths=[ep1, ep2])
        ps.build_index()
        assert ps.api_count == 2
        assert ps.sink_count == 1
        assert len(ps.paths_for_sink(sink.id)) == 2


# ═══════════════════════════════════════════════════════════════════════
# PathGenerator
# ═══════════════════════════════════════════════════════════════════════

class TestPathGenerator:

    def _build_simple_graph(self) -> CallGraph:
        """
        调用图:
          Controller.handle → Service.process → Dao.query
                                              → Logger.logError
        """
        cg = CallGraph()
        cg.add_node(CallGraphNode("com.Controller", "handle"))
        cg.add_node(CallGraphNode("com.Service", "process"))
        cg.add_node(CallGraphNode("com.Dao", "query"))
        cg.add_node(CallGraphNode("com.Logger", "logError"))

        cg.add_edge(CallGraphEdge("com.Controller.handle", "com.Service.process"))
        cg.add_edge(CallGraphEdge("com.Service.process", "com.Dao.query"))
        cg.add_edge(CallGraphEdge("com.Service.process", "com.Logger.logError"))
        return cg

    def test_generates_path(self):
        cg = self._build_simple_graph()
        api = make_api("com.Controller", "handle")
        sink = make_sink("com.Logger", "logError")

        gen = PathGenerator()
        result = gen.generate("test-proj", [api], [sink], cg)

        assert len(result.all_paths) == 1
        path = result.all_paths[0]
        assert path.api_entry == api
        assert path.log_sink == sink
        assert path.path_length == 3
        assert path.method_sequence == [
            "com.Controller.handle",
            "com.Service.process",
            "com.Logger.logError",
        ]

    def test_unreachable_sink(self):
        """Sink 不可达 → 不生成路径."""
        cg = CallGraph()
        cg.add_node(CallGraphNode("com.A", "entry"))
        cg.add_node(CallGraphNode("com.B", "isolated"))
        # 没有边

        api = make_api("com.A", "entry")
        sink = make_sink("com.B", "isolated")

        gen = PathGenerator()
        result = gen.generate("test", [api], [sink], cg)
        assert len(result.all_paths) == 0

    def test_multiple_apis_same_sink(self):
        """多个 API 到达同一个 sink."""
        cg = CallGraph()
        cg.add_node(CallGraphNode("com.A", "get"))
        cg.add_node(CallGraphNode("com.B", "post"))
        cg.add_node(CallGraphNode("com.Log", "warn"))

        cg.add_edge(CallGraphEdge("com.A.get", "com.Log.warn"))
        cg.add_edge(CallGraphEdge("com.B.post", "com.Log.warn"))

        apis = [
            make_api("com.A", "get"),
            make_api("com.B", "post"),
        ]
        sink = make_sink("com.Log", "warn")

        gen = PathGenerator()
        result = gen.generate("test", apis, [sink], cg)
        assert len(result.all_paths) == 2
        assert result.api_count == 2
        assert result.sink_count == 1

    def test_taint_path_preferred(self):
        """有 taint 标记的路径优先选中."""
        cg = CallGraph()
        cg.add_node(CallGraphNode("com.Entry", "handle"))
        cg.add_node(CallGraphNode("com.Short", "direct"))
        cg.add_node(CallGraphNode("com.Long1", "step1"))
        cg.add_node(CallGraphNode("com.Long2", "step2"))
        cg.add_node(CallGraphNode("com.Sink", "log"))

        # 短路径 (无 taint): Entry → Short → Sink
        cg.add_edge(CallGraphEdge("com.Entry.handle", "com.Short.direct"))
        cg.add_edge(CallGraphEdge("com.Short.direct", "com.Sink.log"))

        # 长路径 (有 taint): Entry → Long1 → Long2 → Sink
        cg.add_edge(CallGraphEdge("com.Entry.handle", "com.Long1.step1"))
        cg.add_edge(CallGraphEdge("com.Long1.step1", "com.Long2.step2", is_taint=True))
        cg.add_edge(CallGraphEdge("com.Long2.step2", "com.Sink.log"))

        api = make_api("com.Entry", "handle")
        sink = make_sink("com.Sink", "log")

        gen = PathGenerator(max_paths_per_pair=1, prefer_taint=True)
        result = gen.generate("test", [api], [sink], cg)

        assert len(result.all_paths) == 1
        path = result.all_paths[0]
        assert path.source == PathSource.TAINT
        assert path.path_length == 4  # Entry → Long1 → Long2 → Sink

    def test_max_path_length_limit(self):
        """超过最大深度的路径不会被生成."""
        cg = CallGraph()
        # 构建长链: A → B → C → D → E → Sink
        nodes_names = ["A", "B", "C", "D", "E", "Sink"]
        for n in nodes_names:
            cg.add_node(CallGraphNode(f"com.{n}", "run"))
        for i in range(len(nodes_names) - 1):
            cg.add_edge(CallGraphEdge(
                f"com.{nodes_names[i]}.run",
                f"com.{nodes_names[i+1]}.run"
            ))

        api = make_api("com.A", "run")
        sink = make_sink("com.Sink", "run")

        # 限制 max_path_length=3, 路径需要 6 步 → 找不到
        gen = PathGenerator(max_path_length=3)
        result = gen.generate("test", [api], [sink], cg)
        assert len(result.all_paths) == 0

        # 放宽限制
        gen2 = PathGenerator(max_path_length=10)
        result2 = gen2.generate("test", [api], [sink], cg)
        assert len(result2.all_paths) == 1


# ═══════════════════════════════════════════════════════════════════════
# PathDiffer
# ═══════════════════════════════════════════════════════════════════════

class TestPathDiffer:

    def test_full_match(self):
        """实际 trace 完全匹配预期路径."""
        api = make_api("com.Controller", "handle")
        sink = make_sink("com.Logger", "logError")
        ep = make_expected_path(api, sink, [
            ("com.Controller", "handle"),
            ("com.Service", "process"),
            ("com.Logger", "logError"),
        ])

        # trace: Controller → Service → Logger (父子关系)
        trace = make_trace([
            make_trace_node("s1", "", "com.Controller", "handle", 1000),
            make_trace_node("s2", "s1", "com.Service", "process", 2000),
            make_trace_node("s3", "s2", "com.Logger", "logError", 3000),
        ])

        differ = PathDiffer()
        result = differ.diff(trace, ep)

        assert result.has_divergence is False
        assert result.reached_depth == 2
        assert result.reach_rate == 1.0
        assert result.divergence_reason == "full_reach"

    def test_partial_reach(self):
        """实际 trace 只到达了预期路径的前半段."""
        api = make_api("com.Controller", "handle")
        sink = make_sink("com.Logger", "logError")
        ep = make_expected_path(api, sink, [
            ("com.Controller", "handle"),
            ("com.Service", "process"),
            ("com.Logger", "logError"),
        ])

        # trace: Controller → Service (没有 Logger)
        trace = make_trace([
            make_trace_node("s1", "", "com.Controller", "handle", 1000),
            make_trace_node("s2", "s1", "com.Service", "process", 2000),
        ])

        differ = PathDiffer()
        result = differ.diff(trace, ep)

        assert result.has_divergence is True
        assert result.reached_depth == 1
        assert result.reached_node.qualified_name == "com.Service.process"
        assert result.first_missed_depth == 2
        assert result.first_missed_node.qualified_name == "com.Logger.logError"
        assert result.divergence_reason == "partial_reach"
        assert result.reach_rate == pytest.approx(2 / 3)

    def test_not_started(self):
        """实际 trace 连 entry 都没有匹配."""
        api = make_api("com.Controller", "handle")
        sink = make_sink("com.Logger", "logError")
        ep = make_expected_path(api, sink, [
            ("com.Controller", "handle"),
            ("com.Service", "process"),
            ("com.Logger", "logError"),
        ])

        # trace 是完全不同的方法
        trace = make_trace([
            make_trace_node("s1", "", "com.Other", "unknown", 1000),
        ])

        differ = PathDiffer()
        result = differ.diff(trace, ep)

        assert result.has_divergence is True
        assert result.reached_depth == -1
        assert result.divergence_reason == "not_started"
        assert result.reach_rate == 0.0

    def test_empty_trace(self):
        """空 trace → 不匹配."""
        api = make_api("com.A", "run")
        sink = make_sink("com.B", "log")
        ep = make_expected_path(api, sink, [("com.A", "run"), ("com.B", "log")])

        trace = make_trace([])
        differ = PathDiffer()
        result = differ.diff(trace, ep)

        assert result.has_divergence is True
        assert result.reached_depth == -1

    def test_deep_tree_partial(self):
        """深层调用树中的 partial match."""
        api = make_api("com.A", "a")
        sink = make_sink("com.E", "e")
        ep = make_expected_path(api, sink, [
            ("com.A", "a"),
            ("com.B", "b"),
            ("com.C", "c"),
            ("com.D", "d"),
            ("com.E", "e"),
        ])

        # trace: A → B → C (没有 D, E)
        trace = make_trace([
            make_trace_node("s1", "", "com.A", "a", 1000),
            make_trace_node("s2", "s1", "com.B", "b", 2000),
            make_trace_node("s3", "s2", "com.C", "c", 3000),
        ])

        differ = PathDiffer()
        result = differ.diff(trace, ep)

        assert result.has_divergence is True
        assert result.reached_depth == 2  # A=0, B=1, C=2
        assert result.first_missed_depth == 3
        assert result.first_missed_node.qualified_name == "com.D.d"
        assert result.reach_rate == pytest.approx(3 / 5)

    def test_trace_has_extra_branches(self):
        """trace 有额外分支但预期路径仍然匹配."""
        api = make_api("com.A", "a")
        sink = make_sink("com.C", "c")
        ep = make_expected_path(api, sink, [
            ("com.A", "a"),
            ("com.B", "b"),
            ("com.C", "c"),
        ])

        # trace: A → [B → C, D (extra branch)]
        trace = make_trace([
            make_trace_node("s1", "", "com.A", "a", 1000),
            make_trace_node("s2", "s1", "com.B", "b", 2000),
            make_trace_node("s3", "s2", "com.C", "c", 3000),
            make_trace_node("s4", "s1", "com.D", "d", 4000),  # extra
        ])

        differ = PathDiffer()
        result = differ.diff(trace, ep)

        # 应该仍然是 full match (extra branch 不影响)
        assert result.has_divergence is False
        assert result.reached_depth == 2


# ═══════════════════════════════════════════════════════════════════════
# PathDifferBatch
# ═══════════════════════════════════════════════════════════════════════

class TestPathDifferBatch:

    def test_best_match(self):
        """多条预期路径 → 返回匹配度最高的."""
        api = make_api("com.A", "a")
        sink1 = make_sink("com.X", "logX")
        sink2 = make_sink("com.B", "logB")

        # 路径 1: A → X (trace 中 X 不存在)
        ep1 = make_expected_path(api, sink1, [("com.A", "a"), ("com.X", "logX")])
        # 路径 2: A → B (trace 中 B 存在)
        ep2 = make_expected_path(api, sink2, [("com.A", "a"), ("com.B", "logB")])

        trace = make_trace([
            make_trace_node("s1", "", "com.A", "a", 1000),
            make_trace_node("s2", "s1", "com.B", "logB", 2000),
        ])

        batch = PathDifferBatch()
        best = batch.best_match(trace, [ep1, ep2])

        assert best is not None
        assert best.has_divergence is False  # ep2 完全匹配
        assert best.expected_path.log_sink.id == sink2.id

    def test_all_divergences_sorted(self):
        """批量结果按 reach_rate 降序."""
        api = make_api("com.A", "a")
        # 3 条路径, 各种匹配度
        ep1 = make_expected_path(api, make_sink("com.S1", "l"), [
            ("com.A", "a"), ("com.S1", "l"),
        ])
        ep2 = make_expected_path(api, make_sink("com.S2", "l"), [
            ("com.A", "a"), ("com.B", "b"), ("com.S2", "l"),
        ])
        ep3 = make_expected_path(api, make_sink("com.S3", "l"), [
            ("com.Z", "z"), ("com.S3", "l"),
        ])

        # trace: A → B (没有 S1, S2, Z)
        trace = make_trace([
            make_trace_node("s1", "", "com.A", "a", 1000),
            make_trace_node("s2", "s1", "com.B", "b", 2000),
        ])

        batch = PathDifferBatch()
        results = batch.diff_all(trace, [ep1, ep2, ep3])

        # ep2: A(0) → B(1) → 没有 S2 → reach_rate = 2/3
        # ep1: A(0) → 没有 S1 → reach_rate = 1/2
        # ep3: 没有 Z → reach_rate = 0
        assert results[0].reach_rate == pytest.approx(2 / 3)
        assert results[1].reach_rate == pytest.approx(1 / 2)
        assert results[2].reach_rate == pytest.approx(0.0)
