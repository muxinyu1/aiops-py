"""
test_path_e2e.py — 端到端测试: 真实容器 Trace vs 预期路径偏差计算

前提: PiggyMetrics account-service 容器已启动
  docker compose -f examples-yml/PiggyMetrics/compose.real.yaml up -d

流程:
  1. 从源码中识别 API 入口和日志打印点 (已知)
  2. 构建调用图, 用 PathGenerator 生成预期路径
  3. 向真实容器发 HTTP 请求 (带 X-Return-Trace: true)
  4. 解析响应头中的 X-Execution-Trace 为 Trace 对象
  5. 用 PathDiffer 比较实际 Trace 与预期路径, 计算偏差
"""

import requests
import base64
import json

from trace import Trace, TraceNode
from expected_path import APIEntry, LogSink, PathNode, ExpectedPath, ExpectedPathSet, PathSource
from path_generator import CallGraph, CallGraphNode, CallGraphEdge, PathGenerator
from path_differ import PathDiffer, PathDifferBatch


BASE_URL = "http://127.0.0.1:8080"


# ═══════════════════════════════════════════════════════════════════════
# Step 1: 已知的 API 入口和日志打印点 (模拟源码扫描结果)
# ═══════════════════════════════════════════════════════════════════════

def build_api_entries() -> list[APIEntry]:
    return [
        APIEntry(
            class_name="com.piggymetrics.account.controller.AccountController",
            method="createNewAccount",
            src_file="AccountController.java",
            line_number=37,
            http_method="POST",
            http_path="/",
        ),
        APIEntry(
            class_name="com.piggymetrics.account.controller.AccountController",
            method="saveCurrentAccount",
            src_file="AccountController.java",
            line_number=31,
            http_method="PUT",
            http_path="/current",
        ),
        APIEntry(
            class_name="com.piggymetrics.account.controller.AccountController",
            method="getAccountByName",
            src_file="AccountController.java",
            line_number=21,
            http_method="GET",
            http_path="/{name}",
        ),
    ]


def build_log_sinks() -> list[LogSink]:
    """AccountServiceImpl 中的日志打印点."""
    return [
        LogSink(
            class_name="com.piggymetrics.account.service.AccountServiceImpl",
            method="create",
            src_file="AccountServiceImpl.java",
            line_number=67,
            log_level="INFO",
            log_message_template="new account has been created: {}",
            log_api="log.info",
        ),
        LogSink(
            class_name="com.piggymetrics.account.service.AccountServiceImpl",
            method="saveChanges",
            src_file="AccountServiceImpl.java",
            line_number=83,
            log_level="DEBUG",
            log_message_template="account {} changes has been saved",
            log_api="log.debug",
        ),
    ]


# ═══════════════════════════════════════════════════════════════════════
# Step 2: 调用图 + 预期路径生成
# ═══════════════════════════════════════════════════════════════════════

def build_call_graph() -> CallGraph:
    """PiggyMetrics account-service 调用图."""
    cg = CallGraph()

    methods = [
        ("com.piggymetrics.account.controller.AccountController", "createNewAccount"),
        ("com.piggymetrics.account.controller.AccountController", "saveCurrentAccount"),
        ("com.piggymetrics.account.controller.AccountController", "getAccountByName"),
        ("com.piggymetrics.account.service.AccountServiceImpl", "findByName"),
        ("com.piggymetrics.account.service.AccountServiceImpl", "create"),
        ("com.piggymetrics.account.service.AccountServiceImpl", "saveChanges"),
        ("com.piggymetrics.account.repository.AccountRepository", "findByName"),
        ("com.piggymetrics.account.repository.AccountRepository", "save"),
        ("com.piggymetrics.account.client.AuthServiceClient", "createUser"),
        ("com.piggymetrics.account.client.StatisticsServiceClient", "updateStatistics"),
    ]
    for cn, m in methods:
        cg.add_node(CallGraphNode(class_name=cn, method=m))

    edges = [
        ("com.piggymetrics.account.controller.AccountController.createNewAccount",
         "com.piggymetrics.account.service.AccountServiceImpl.create"),
        ("com.piggymetrics.account.controller.AccountController.saveCurrentAccount",
         "com.piggymetrics.account.service.AccountServiceImpl.saveChanges"),
        ("com.piggymetrics.account.controller.AccountController.getAccountByName",
         "com.piggymetrics.account.service.AccountServiceImpl.findByName"),
        ("com.piggymetrics.account.service.AccountServiceImpl.findByName",
         "com.piggymetrics.account.repository.AccountRepository.findByName"),
        ("com.piggymetrics.account.service.AccountServiceImpl.create",
         "com.piggymetrics.account.client.AuthServiceClient.createUser"),
        ("com.piggymetrics.account.service.AccountServiceImpl.create",
         "com.piggymetrics.account.repository.AccountRepository.save"),
        ("com.piggymetrics.account.service.AccountServiceImpl.saveChanges",
         "com.piggymetrics.account.repository.AccountRepository.findByName"),
        ("com.piggymetrics.account.service.AccountServiceImpl.saveChanges",
         "com.piggymetrics.account.repository.AccountRepository.save"),
        ("com.piggymetrics.account.service.AccountServiceImpl.saveChanges",
         "com.piggymetrics.account.client.StatisticsServiceClient.updateStatistics"),
    ]
    for caller, callee in edges:
        cg.add_edge(CallGraphEdge(caller_id=caller, callee_id=callee))

    return cg


# ═══════════════════════════════════════════════════════════════════════
# Step 3: 发真实 HTTP 请求获取 Trace
# ═══════════════════════════════════════════════════════════════════════

def send_request(method: str, path: str, json_body: dict = None) -> Trace | None:
    """
    向真实容器发请求, 解析 X-Execution-Trace 响应头为 Trace 对象.
    """
    url = f"{BASE_URL}{path}"
    headers = {"X-Return-Trace": "true"}

    try:
        if method == "POST":
            resp = requests.post(url, json=json_body, headers=headers, timeout=10)
        elif method == "PUT":
            resp = requests.put(url, json=json_body, headers=headers, timeout=10)
        elif method == "GET":
            resp = requests.get(url, headers=headers, timeout=10)
        else:
            return None
    except requests.RequestException as e:
        print(f"    ❌ Request failed: {e}")
        return None

    print(f"    HTTP {resp.status_code}")

    # 解析 X-Execution-Trace
    trace_header = resp.headers.get("X-Execution-Trace", "")
    if not trace_header:
        print(f"    ⚠️ No X-Execution-Trace header in response")
        return None

    # 解析为 Trace
    nodes = Trace.parse_execution_trace(trace_header)
    trace = Trace(source=None, sink=None, nodes=nodes)
    trace.build_tree()
    return trace


# ═══════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("PiggyMetrics 端到端测试: 真实 Trace vs 预期路径")
    print("=" * 70)

    # ── 生成预期路径 ──────────────────────────────────────────────
    apis = build_api_entries()
    sinks = build_log_sinks()
    cg = build_call_graph()

    gen = PathGenerator(max_path_length=10, max_paths_per_pair=1)
    path_set = gen.generate("PiggyMetrics-account-service", apis, sinks, cg)

    print(f"\n[预期路径] {path_set.stats}")
    for ep in path_set.all_paths:
        print(f"  📍 {ep.api_entry.id} → {ep.log_sink.id}")
        print(f"     Sequence: {' → '.join(n.method for n in ep.nodes)}")
    print()

    # ── Test Case 1: POST / (创建账户 — 预期到达 log.info) ────────
    print("-" * 70)
    print("[Test 1] POST / — 创建账户 (预期: createNewAccount → create)")
    print(f"  预期路径目标: log.info(\"new account has been created\")")
    print(f"  发送请求...")

    trace1 = send_request("POST", "/", {"username": "e2e_user_1", "password": "pass123"})
    if trace1:
        print(f"    Trace nodes: {len(trace1.nodes)}")
        for n in trace1.nodes:
            err = f" [ERROR: {n.error_message}]" if n.is_error else ""
            print(f"      {n.class_namespace}.{n.function}{err}")

        # 做偏差计算
        create_paths = path_set.paths_for_api("POST /")
        if create_paths:
            differ = PathDiffer()
            result = differ.diff(trace1, create_paths[0])
            print(f"\n  [偏差结果]")
            print(f"    {result.summary}")
            print(f"    Reach rate: {result.reach_rate:.0%}")
            if result.first_missed_node:
                print(f"    First missed: {result.first_missed_node.qualified_name}")
            print(f"    Reason: {result.divergence_reason}")

    # ── Test Case 2: GET /demo (不存在的 API — 预期无路径) ────────
    print("\n" + "-" * 70)
    print("[Test 2] GET /demo — 非业务 API (预期: 无预期路径)")

    trace2 = send_request("GET", "/demo")
    if trace2:
        print(f"    Trace nodes: {len(trace2.nodes)}")
        # demo 端点无预期路径
        demo_paths = path_set.paths_for_api("GET /demo")
        print(f"    Expected paths: {len(demo_paths)} (应为 0)")

    # ── Test Case 3: GET /{name} (查询账户 — 无日志打印点路径) ────
    print("\n" + "-" * 70)
    print("[Test 3] GET /demo — 获取账户 (预期: 该 API 无日志打印点可达)")

    trace3 = send_request("GET", "/demo")
    if trace3:
        print(f"    Trace nodes: {len(trace3.nodes)}")
    get_paths = path_set.paths_for_api("GET /{name}")
    print(f"    Expected paths for GET /{{name}}: {len(get_paths)}")
    if not get_paths:
        print(f"    ✓ 正确: findByName 方法内无日志打印")

    # ── 总结 ─────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("总结:")
    print(f"  预期路径: {len(path_set.all_paths)} 条 (覆盖 {path_set.api_count} 个 API)")
    if trace1:
        create_paths = path_set.paths_for_api("POST /")
        if create_paths:
            result = PathDiffer().diff(trace1, create_paths[0])
            print(f"  POST / 实际 trace:")
            print(f"    - 方法链: {' → '.join(n.function for n in trace1.nodes)}")
            print(f"    - 到达 sink 方法: {'是' if result.reached_depth == len(create_paths[0].nodes) - 1 else '否'}")
            print(f"    - 异常原因: {trace1.nodes[0].error_message if trace1.nodes and trace1.nodes[0].is_error else '无'}")
            print(f"    - Reach rate: {result.reach_rate:.0%}")
            print(f"    - LLM 反馈: 请求到达了 {result.reached_node.qualified_name if result.reached_node else 'N/A'},")
            if result.has_divergence and result.first_missed_node:
                print(f"      但未能触发日志打印点. 偏离在 {result.first_missed_node.qualified_name}")
            else:
                print(f"      已到达日志打印点所在方法.")
    print("=" * 70)


if __name__ == "__main__":
    main()
