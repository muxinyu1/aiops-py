"""
test_static_analysis.py — 静态分析集成测试

测试 Joern/CodeQL 适配层的解析逻辑 (不需要实际安装工具).
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from path_generator import CallGraph, CallGraphNode, CallGraphEdge, PathGenerator
from expected_path import APIEntry, LogSink, ExpectedPathSet
from joern_adapter import JoernAdapter, JoernConfig, load_call_graph_from_json
from codeql_adapter import CodeQLAdapter, CodeQLConfig, TaintResult
from static_analysis import StaticAnalysisResult


def test_joern_json_parsing():
    """测试 Joern JSON 输出的解析逻辑."""
    print("\n" + "=" * 60)
    print("TEST 1: Joern JSON 解析")
    print("=" * 60)

    # 模拟 Joern 导出的 JSON (java-microservice 项目)
    mock_joern_output = {
        "methods": [
            {
                "fullName": "com.example.microservice.controller.AppController.getUser",
                "name": "getUser",
                "signature": "ResponseEntity(Long)",
                "filename": "AppController.java",
                "lineNumber": 37,
                "className": "com.example.microservice.controller.AppController"
            },
            {
                "fullName": "com.example.microservice.service.UserService.findById",
                "name": "findById",
                "signature": "Map(Long)",
                "filename": "UserService.java",
                "lineNumber": 30,
                "className": "com.example.microservice.service.UserService"
            },
            {
                "fullName": "com.example.microservice.service.UserService.create",
                "name": "create",
                "signature": "Map(String,String)",
                "filename": "UserService.java",
                "lineNumber": 40,
                "className": "com.example.microservice.service.UserService"
            },
            {
                "fullName": "com.example.microservice.controller.AppController.createUser",
                "name": "createUser",
                "signature": "ResponseEntity(Map)",
                "filename": "AppController.java",
                "lineNumber": 50,
                "className": "com.example.microservice.controller.AppController"
            },
            {
                "fullName": "com.example.microservice.model.ApiResponse.success",
                "name": "success",
                "signature": "ApiResponse(Object)",
                "filename": "ApiResponse.java",
                "lineNumber": 16,
                "className": "com.example.microservice.model.ApiResponse"
            },
            {
                "fullName": "com.example.microservice.model.ApiResponse.error",
                "name": "error",
                "signature": "ApiResponse(String)",
                "filename": "ApiResponse.java",
                "lineNumber": 22,
                "className": "com.example.microservice.model.ApiResponse"
            },
        ],
        "calls": [
            {
                "caller": "com.example.microservice.controller.AppController.getUser",
                "callee": "com.example.microservice.service.UserService.findById",
                "line": 39
            },
            {
                "caller": "com.example.microservice.controller.AppController.getUser",
                "callee": "com.example.microservice.model.ApiResponse.success",
                "line": 42
            },
            {
                "caller": "com.example.microservice.controller.AppController.getUser",
                "callee": "com.example.microservice.model.ApiResponse.error",
                "line": 44
            },
            {
                "caller": "com.example.microservice.controller.AppController.createUser",
                "callee": "com.example.microservice.service.UserService.create",
                "line": 52
            },
            {
                "caller": "com.example.microservice.controller.AppController.createUser",
                "callee": "com.example.microservice.model.ApiResponse.success",
                "line": 55
            },
        ]
    }

    # 写入临时 JSON
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(mock_joern_output, f)
        json_path = f.name

    try:
        # 解析
        cg = load_call_graph_from_json(json_path, package_filter="com.example.microservice")

        print(f"\n  Methods: {len(cg.nodes)}")
        for node_id, node in sorted(cg.nodes.items()):
            print(f"    {node_id} ({node.src_file}:{node.line_number})")

        print(f"\n  Edges: {len(cg.edges)}")
        for edge in cg.edges:
            print(f"    {edge.caller_id} → {edge.callee_id} (line {edge.call_line})")

        assert len(cg.nodes) == 6, f"Expected 6 nodes, got {len(cg.nodes)}"
        assert len(cg.edges) == 5, f"Expected 5 edges, got {len(cg.edges)}"

        # 验证 adjacency
        successors = cg.successors("com.example.microservice.controller.AppController.getUser")
        assert len(successors) == 3, f"Expected 3 successors, got {len(successors)}"

        print("\n✅ Joern JSON 解析正确")
    finally:
        os.unlink(json_path)


def test_codeql_taint_marking():
    """测试 CodeQL taint 结果标记逻辑."""
    print("\n" + "=" * 60)
    print("TEST 2: CodeQL Taint 标记")
    print("=" * 60)

    # 手动构建一个 CallGraph
    cg = CallGraph()
    cg.add_node(CallGraphNode("com.example.Controller", "handle"))
    cg.add_node(CallGraphNode("com.example.Service", "process"))
    cg.add_node(CallGraphNode("com.example.Dao", "query"))
    cg.add_node(CallGraphNode("com.example.Logger", "logError"))

    cg.add_edge(CallGraphEdge("com.example.Controller.handle", "com.example.Service.process"))
    cg.add_edge(CallGraphEdge("com.example.Service.process", "com.example.Dao.query"))
    cg.add_edge(CallGraphEdge("com.example.Service.process", "com.example.Logger.logError"))
    cg.add_edge(CallGraphEdge("com.example.Dao.query", "com.example.Logger.logError"))
    cg.build_adjacency()

    # 模拟 CodeQL 结果 CSV
    csv_content = (
        "location,pair\n"
        '"Service.java:15","com.example.Controller.handle|com.example.Service.process"\n'
        '"Service.java:20","com.example.Service.process|com.example.Dao.query"\n'
    )

    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        f.write(csv_content)
        csv_path = f.name

    try:
        # 标记 taint
        config = CodeQLConfig()
        adapter = CodeQLAdapter(config=config)
        result = adapter.mark_taint_edges_from_results(cg, csv_path)

        print(f"\n  Total taint pairs: {result.total_taint_pairs}")
        print(f"  Marked edges: {result.marked_edges}")

        # 验证
        assert result.total_taint_pairs == 2
        assert result.marked_edges == 2

        # 验证具体哪些边被标记
        taint_edges = [(e.caller_id, e.callee_id) for e in cg.edges if e.is_taint]
        print(f"  Taint edges: {taint_edges}")
        assert ("com.example.Controller.handle", "com.example.Service.process") in taint_edges
        assert ("com.example.Service.process", "com.example.Dao.query") in taint_edges

        # 验证 has_taint 方法
        assert cg.has_taint("com.example.Controller.handle", "com.example.Service.process")
        assert cg.has_taint("com.example.Service.process", "com.example.Dao.query")
        assert not cg.has_taint("com.example.Service.process", "com.example.Logger.logError")

        print("\n✅ CodeQL Taint 标记正确")
    finally:
        os.unlink(csv_path)


def test_full_pipeline_with_mock():
    """测试完整流水线 (mock 数据, 不需要实际工具)."""
    print("\n" + "=" * 60)
    print("TEST 3: 完整流水线 (mock)")
    print("=" * 60)

    # 模拟 Joern 输出
    mock_joern = {
        "methods": [
            {"fullName": "com.example.Controller.handle", "name": "handle",
             "signature": "Response(Request)", "filename": "Controller.java",
             "lineNumber": 10, "className": "com.example.Controller"},
            {"fullName": "com.example.Service.process", "name": "process",
             "signature": "void(String)", "filename": "Service.java",
             "lineNumber": 20, "className": "com.example.Service"},
            {"fullName": "com.example.Dao.query", "name": "query",
             "signature": "Result(String)", "filename": "Dao.java",
             "lineNumber": 30, "className": "com.example.Dao"},
            {"fullName": "com.example.Logger.logError", "name": "logError",
             "signature": "void(String)", "filename": "Logger.java",
             "lineNumber": 40, "className": "com.example.Logger"},
        ],
        "calls": [
            {"caller": "com.example.Controller.handle", "callee": "com.example.Service.process", "line": 12},
            {"caller": "com.example.Service.process", "callee": "com.example.Dao.query", "line": 22},
            {"caller": "com.example.Service.process", "callee": "com.example.Logger.logError", "line": 25},
            {"caller": "com.example.Dao.query", "callee": "com.example.Logger.logError", "line": 35},
        ]
    }

    # 模拟 CodeQL taint CSV
    mock_taint_csv = (
        "location,pair\n"
        '"Controller.java:12","com.example.Controller.handle|com.example.Service.process"\n'
        '"Service.java:22","com.example.Service.process|com.example.Dao.query"\n'
        '"Dao.java:35","com.example.Dao.query|com.example.Logger.logError"\n'
    )

    # 写入临时文件
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(mock_joern, f)
        joern_json = f.name

    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        f.write(mock_taint_csv)
        taint_csv = f.name

    try:
        # Step 1: 加载调用图
        cg = load_call_graph_from_json(joern_json, package_filter="com.example")

        # Step 2: 标记 taint
        config = CodeQLConfig()
        adapter = CodeQLAdapter(config=config)
        taint_result = adapter.mark_taint_edges_from_results(cg, taint_csv)

        # Step 3: 生成路径
        api_entries = [
            APIEntry(
                class_name="com.example.Controller",
                method="handle",
                http_method="GET",
                http_path="/api/data",
            )
        ]
        log_sinks = [
            LogSink(
                class_name="com.example.Logger",
                method="logError",
                log_level="ERROR",
                log_api="log.error",
            )
        ]

        generator = PathGenerator()
        path_set = generator.generate(
            project_name="test-project",
            api_entries=api_entries,
            log_sinks=log_sinks,
            call_graph=cg,
        )

        print(f"\n  CallGraph: {len(cg.nodes)} nodes, {len(cg.edges)} edges")
        print(f"  Taint: {taint_result.summary}")
        print(f"  Paths found: {len(path_set.all_paths)}")

        for path in path_set.all_paths:
            print(f"\n  Path: {path.api_entry.id} → {path.log_sink.id}")
            print(f"    Source: {path.source.value}")
            print(f"    Confidence: {path.confidence}")
            print(f"    Nodes: {' → '.join(path.method_sequence)}")

        # 验证: 应该找到 taint 路径 (通过 Dao)
        assert len(path_set.all_paths) >= 1, "Should find at least 1 path"

        # 最优路径应该是 taint 路径
        best_path = path_set.all_paths[0]
        assert best_path.source.value == "taint", \
            f"Best path should be taint, got {best_path.source.value}"
        assert best_path.confidence == 0.8

        print(f"\n  ✓ 最优路径是 taint 路径 (置信度 {best_path.confidence})")
        print("\n✅ 完整流水线工作正常")
    finally:
        os.unlink(joern_json)
        os.unlink(taint_csv)


def test_docker_joern_availability():
    """测试 Docker 模式 Joern 是否可用."""
    print("\n" + "=" * 60)
    print("TEST 4: Docker Joern 可用性")
    print("=" * 60)

    from joern_adapter import JoernDockerAdapter, JoernConfig
    adapter = JoernDockerAdapter(config=JoernConfig())

    if adapter.is_available():
        print("  ✓ Docker available — can use JoernDockerAdapter")
        print("    Image: ghcr.io/joernio/joern")
    else:
        print("  ✗ Docker not available")

    print("\n✅ Docker 检查完成")


def main():
    print("🔬 静态分析集成测试")
    print("   (使用 mock 数据, 不需要实际安装 Joern/CodeQL)")

    test_joern_json_parsing()
    test_codeql_taint_marking()
    test_full_pipeline_with_mock()
    test_docker_joern_availability()

    print("\n" + "=" * 60)
    print("🎉 全部测试通过!")
    print("=" * 60)


if __name__ == "__main__":
    main()
