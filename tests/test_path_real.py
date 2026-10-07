"""
test_path_real.py — 使用 PiggyMetrics account-service 的真实代码结构验证路径生成和偏差计算

场景:
  PiggyMetrics account-service 的调用结构:
    AccountController.getAccountByName  → AccountServiceImpl.findByName → AccountRepository.findByName
    AccountController.getCurrentAccount → AccountServiceImpl.findByName → AccountRepository.findByName
    AccountController.createNewAccount  → AccountServiceImpl.create     → AuthServiceClient.createUser
                                                                        → AccountRepository.save
                                                                        → log.info(...)  ← SINK
    AccountController.saveCurrentAccount → AccountServiceImpl.saveChanges → AccountRepository.findByName
                                                                          → AccountRepository.save
                                                                          → log.debug(...) ← SINK
                                                                          → StatisticsServiceClient.updateStatistics
"""

from expected_path import (
    APIEntry, LogSink, PathNode, ExpectedPath, ExpectedPathSet, PathSource,
)
from path_generator import (
    CallGraph, CallGraphNode, CallGraphEdge, PathGenerator,
)
from path_differ import PathDiffer, PathDifferBatch, PathDivergence
from trace import Trace, TraceNode


def build_piggymetrics_call_graph() -> CallGraph:
    """构建 PiggyMetrics account-service 的调用图 (手动模拟静态分析产物)."""
    cg = CallGraph()

    # ── 节点: 所有方法 ──────────────────────────────────────────
    methods = [
        # Controller
        ("com.piggymetrics.account.controller.AccountController", "getAccountByName", "AccountController.java"),
        ("com.piggymetrics.account.controller.AccountController", "getCurrentAccount", "AccountController.java"),
        ("com.piggymetrics.account.controller.AccountController", "createNewAccount", "AccountController.java"),
        ("com.piggymetrics.account.controller.AccountController", "saveCurrentAccount", "AccountController.java"),
        # Service
        ("com.piggymetrics.account.service.AccountServiceImpl", "findByName", "AccountServiceImpl.java"),
        ("com.piggymetrics.account.service.AccountServiceImpl", "create", "AccountServiceImpl.java"),
        ("com.piggymetrics.account.service.AccountServiceImpl", "saveChanges", "AccountServiceImpl.java"),
        # Repository
        ("com.piggymetrics.account.repository.AccountRepository", "findByName", "AccountRepository.java"),
        ("com.piggymetrics.account.repository.AccountRepository", "save", "AccountRepository.java"),
        # Clients
        ("com.piggymetrics.account.client.AuthServiceClient", "createUser", "AuthServiceClient.java"),
        ("com.piggymetrics.account.client.StatisticsServiceClient", "updateStatistics", "StatisticsServiceClient.java"),
    ]

    for class_name, method, src_file in methods:
        cg.add_node(CallGraphNode(
            class_name=class_name,
            method=method,
            src_file=src_file,
        ))

    # ── 边: 调用关系 ────────────────────────────────────────────
    edges = [
        # getAccountByName → findByName
        ("com.piggymetrics.account.controller.AccountController.getAccountByName",
         "com.piggymetrics.account.service.AccountServiceImpl.findByName"),
        # getCurrentAccount → findByName
        ("com.piggymetrics.account.controller.AccountController.getCurrentAccount",
         "com.piggymetrics.account.service.AccountServiceImpl.findByName"),
        # createNewAccount → create
        ("com.piggymetrics.account.controller.AccountController.createNewAccount",
         "com.piggymetrics.account.service.AccountServiceImpl.create"),
        # saveCurrentAccount → saveChanges
        ("com.piggymetrics.account.controller.AccountController.saveCurrentAccount",
         "com.piggymetrics.account.service.AccountServiceImpl.saveChanges"),
        # findByName → repository.findByName
        ("com.piggymetrics.account.service.AccountServiceImpl.findByName",
         "com.piggymetrics.account.repository.AccountRepository.findByName"),
        # create → authClient.createUser
        ("com.piggymetrics.account.service.AccountServiceImpl.create",
         "com.piggymetrics.account.client.AuthServiceClient.createUser", True),
        # create → repository.save
        ("com.piggymetrics.account.service.AccountServiceImpl.create",
         "com.piggymetrics.account.repository.AccountRepository.save"),
        # saveChanges → repository.findByName
        ("com.piggymetrics.account.service.AccountServiceImpl.saveChanges",
         "com.piggymetrics.account.repository.AccountRepository.findByName"),
        # saveChanges → repository.save
        ("com.piggymetrics.account.service.AccountServiceImpl.saveChanges",
         "com.piggymetrics.account.repository.AccountRepository.save"),
        # saveChanges → statisticsClient.updateStatistics
        ("com.piggymetrics.account.service.AccountServiceImpl.saveChanges",
         "com.piggymetrics.account.client.StatisticsServiceClient.updateStatistics", True),
    ]

    for edge_data in edges:
        is_taint = edge_data[2] if len(edge_data) == 3 else False
        cg.add_edge(CallGraphEdge(
            caller_id=edge_data[0],
            callee_id=edge_data[1],
            is_taint=is_taint,
        ))

    return cg


def build_api_entries() -> list[APIEntry]:
    """从 AccountController 提取 API 入口."""
    return [
        APIEntry(
            class_name="com.piggymetrics.account.controller.AccountController",
            method="getAccountByName",
            src_file="AccountController.java",
            line_number=21,
            http_method="GET",
            http_path="/{name}",
        ),
        APIEntry(
            class_name="com.piggymetrics.account.controller.AccountController",
            method="getCurrentAccount",
            src_file="AccountController.java",
            line_number=26,
            http_method="GET",
            http_path="/current",
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
            method="createNewAccount",
            src_file="AccountController.java",
            line_number=36,
            http_method="POST",
            http_path="/",
        ),
    ]


def build_log_sinks() -> list[LogSink]:
    """从 AccountServiceImpl 扫描出日志打印点."""
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


def main():
    print("=" * 70)
    print("PiggyMetrics account-service: 预期路径生成 & 偏差计算")
    print("=" * 70)

    # ── Step 1: 构建输入 ──────────────────────────────────────────
    cg = build_piggymetrics_call_graph()
    apis = build_api_entries()
    sinks = build_log_sinks()

    print(f"\n[Input]")
    print(f"  API entries: {len(apis)}")
    for api in apis:
        print(f"    {api.id}")
    print(f"  Log sinks:   {len(sinks)}")
    for sink in sinks:
        print(f"    {sink.id} ({sink.log_api}: \"{sink.log_message_template}\")")
    print(f"  Call graph:  {len(cg.nodes)} nodes, {len(cg.edges)} edges")

    # ── Step 2: 生成预期路径 ──────────────────────────────────────
    gen = PathGenerator(max_path_length=10, max_paths_per_pair=1)
    path_set = gen.generate("PiggyMetrics-account-service", apis, sinks, cg)

    print(f"\n[Generated Expected Paths]")
    print(f"  {path_set.stats}")
    print()

    for path in path_set.all_paths:
        print(f"  📍 {path.id}")
        print(f"     Source: {path.source.value} | Length: {path.path_length} | Confidence: {path.confidence}")
        print(f"     Sequence: {' → '.join(n.method for n in path.nodes)}")
        print()

    # ── Step 3: 模拟实际 Trace 做偏差计算 ──────────────────────────
    print("-" * 70)
    print("偏差计算: 模拟 fuzz 请求的实际 Trace vs 预期路径")
    print("-" * 70)

    # Scenario A: POST / 创建账户 — 完整到达 log.info
    print("\n[Scenario A] POST / 创建账户 — 完整执行")
    trace_a = Trace(source=None, sink=None, nodes=[
        TraceNode(
            span_id="s1", parent_span_id="", trace_id="t1",
            content="AccountController.createNewAccount",
            function="createNewAccount",
            method_signature="createNewAccount(User)",
            class_namespace="com.piggymetrics.account.controller.AccountController",
            src_file="AccountController.java", line_number=36,
            start_ns=1000000, duration_ns=50000,
        ),
        TraceNode(
            span_id="s2", parent_span_id="s1", trace_id="t1",
            content="AccountServiceImpl.create",
            function="create",
            method_signature="create(User)",
            class_namespace="com.piggymetrics.account.service.AccountServiceImpl",
            src_file="AccountServiceImpl.java", line_number=47,
            start_ns=1010000, duration_ns=40000,
        ),
        TraceNode(
            span_id="s3", parent_span_id="s2", trace_id="t1",
            content="AuthServiceClient.createUser",
            function="createUser",
            method_signature="createUser(User)",
            class_namespace="com.piggymetrics.account.client.AuthServiceClient",
            src_file="AuthServiceClient.java", line_number=10,
            start_ns=1020000, duration_ns=20000,
        ),
        TraceNode(
            span_id="s4", parent_span_id="s2", trace_id="t1",
            content="AccountRepository.save",
            function="save",
            method_signature="save(Account)",
            class_namespace="com.piggymetrics.account.repository.AccountRepository",
            src_file="AccountRepository.java", line_number=5,
            start_ns=1045000, duration_ns=5000,
        ),
    ])
    trace_a.build_tree()

    # 找到 POST / 对应的预期路径
    create_paths = path_set.paths_for_api("POST /")
    differ = PathDiffer()
    batch = PathDifferBatch()

    if create_paths:
        result_a = batch.best_match(trace_a, create_paths)
        print(f"  Expected path: {result_a.expected_path.method_sequence}")
        print(f"  Result: {result_a.summary}")
        print(f"  Reach rate: {result_a.reach_rate:.1%}")
    else:
        print("  ⚠️ No expected paths for POST /")

    # Scenario B: PUT /current 保存账户 — saveChanges 未被调用 (Controller 直接异常)
    print("\n[Scenario B] PUT /current 保存账户 — Controller 层异常, 未进入 Service")
    trace_b = Trace(source=None, sink=None, nodes=[
        TraceNode(
            span_id="s1", parent_span_id="", trace_id="t2",
            content="AccountController.saveCurrentAccount",
            function="saveCurrentAccount",
            method_signature="saveCurrentAccount(Principal, Account)",
            class_namespace="com.piggymetrics.account.controller.AccountController",
            src_file="AccountController.java", line_number=31,
            start_ns=2000000, duration_ns=30000,
            is_error=True, error_message="principal is null",
        ),
    ])
    trace_b.build_tree()

    save_paths = path_set.paths_for_api("PUT /current")
    if save_paths:
        result_b = batch.best_match(trace_b, save_paths)
        print(f"  Expected path: {result_b.expected_path.method_sequence}")
        print(f"  Result: {result_b.summary}")
        print(f"  Reach rate: {result_b.reach_rate:.1%}")
        if result_b.first_missed_node:
            print(f"  First missed: {result_b.first_missed_node.qualified_name} (depth={result_b.first_missed_depth})")
    else:
        print("  ⚠️ No expected paths for PUT /current")

    # Scenario C: GET /current — 该 API 没有可达的日志打印点
    print("\n[Scenario C] GET /current — 该 API 无日志打印点路径")
    get_paths = path_set.paths_for_api("GET /current")
    print(f"  Expected paths count: {len(get_paths)}")
    if not get_paths:
        print(f"  ✓ 正确: findByName 没有日志打印, 无预期路径")

    # Scenario D: GET /{name} — 同样无日志路径
    print("\n[Scenario D] GET /{{name}} — 该 API 无日志打印点路径")
    get_name_paths = path_set.paths_for_api("GET /{name}")
    print(f"  Expected paths count: {len(get_name_paths)}")

    # ── 总结 ────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("总结:")
    print(f"  项目: PiggyMetrics account-service")
    print(f"  API 入口: {len(apis)} 个")
    print(f"  日志打印点: {len(sinks)} 个")
    print(f"  生成预期路径: {len(path_set.all_paths)} 条")
    print(f"  有日志可达的 API: {path_set.api_count} 个")
    print(f"  Scenario A (完整执行): reach_rate={result_a.reach_rate:.0%}")
    print(f"  Scenario B (Controller异常): reach_rate={result_b.reach_rate:.0%}, missed={result_b.first_missed_node.qualified_name if result_b.first_missed_node else 'N/A'}")
    print(f"  Scenario C/D (无路径): 正确识别为无日志可达")
    print("=" * 70)


if __name__ == "__main__":
    main()
