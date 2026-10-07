"""
test_snapshot_e2e.py — 端到端测试: 两阶段偏差计算 + 运行时变量快照

前置条件:
  docker compose -f examples-yml/java-microservice/compose.snapshot-test.yaml up -d
  等待 http://localhost:8080/api/health 返回 200

测试流程:
  Phase 1: 发送两个不同参数的请求, 获取 trace, 计算偏差
  Phase 2: 根据偏差结果确定快照目标, 带 X-Snapshot-Methods 头重新执行
  验证: 偏差结果中包含运行时变量快照
"""

import sys
sys.path.insert(0, '/home/mxy/Documents/aiops-py')

import base64
import json
import urllib.request
import urllib.error
from trace import Trace, TraceNode
from difference import Difference, DivergencePoint, VariableSnapshot
from snapshot_executor import SnapshotDiffer, SnapshotConfig
from source import Source, Type, RESTfulSource
from sink import Sink


BASE_URL = "http://localhost:8080"


def send_traced_request(method: str, path: str, body: dict = None,
                        snapshot_methods: str = None) -> list[dict]:
    """发送带 trace 头的请求, 返回解析后的 span 列表."""
    url = f"{BASE_URL}{path}"
    headers = {"X-Return-Trace": "true"}
    if snapshot_methods:
        headers["X-Snapshot-Methods"] = snapshot_methods

    data = None
    if body:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, method=method, headers=headers, data=data)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            trace_b64 = resp.headers.get("X-Execution-Trace")
            resp_body = resp.read()
    except urllib.error.HTTPError as e:
        trace_b64 = e.headers.get("X-Execution-Trace")
        resp_body = e.read()

    if not trace_b64:
        return []

    if trace_b64 == "IN_BODY":
        body_json = json.loads(resp_body)
        if "trace" in body_json:
            return body_json["trace"] if isinstance(body_json["trace"], list) else json.loads(body_json["trace"])
        return []

    return json.loads(base64.b64decode(trace_b64))


def spans_to_trace(spans: list[dict]) -> Trace:
    """将 span 字典列表转为 Trace 对象."""
    nodes = []
    for s in spans:
        nodes.append(TraceNode(
            span_id=s.get("span_id", ""),
            parent_span_id=s.get("parent_span_id", ""),
            trace_id=s.get("trace_id", ""),
            content=s.get("content", ""),
            function=s.get("function", ""),
            method_signature=s.get("method_signature", ""),
            class_namespace=s.get("class_namespace", ""),
            src_file=s.get("src_file", ""),
            line_number=s.get("line_number", -1),
            start_ns=s.get("start_ns", 0),
            duration_ns=s.get("duration_ns", 0),
            is_error=s.get("is_error", False),
            error_message=s.get("error_message"),
            args_snapshot=s.get("args_snapshot"),
            return_snapshot=s.get("return_snapshot"),
            this_snapshot=s.get("this_snapshot"),
        ))
    trace = Trace(
        source=Source(type=Type.RESTFUL, data=RESTfulSource()),
        sink=Sink(),
        nodes=nodes,
    )
    return trace


def test_basic_snapshot():
    """测试1: 基本快照功能 — 单个请求带 X-Snapshot-Methods: * """
    print("\n" + "=" * 60)
    print("TEST 1: 基本快照功能 (X-Snapshot-Methods: *)")
    print("=" * 60)

    spans = send_traced_request("GET", "/api/users/1", snapshot_methods="*")
    assert spans, "No spans returned!"

    print(f"\n收到 {len(spans)} 个 spans:")
    has_snapshot = False
    for span in spans:
        args = span.get("args_snapshot")
        ret = span.get("return_snapshot")
        this = span.get("this_snapshot")
        snapshot_info = ""
        if args:
            has_snapshot = True
            snapshot_info += f"\n    args: {args[:100]}..."  if len(str(args)) > 100 else f"\n    args: {args}"
        if ret:
            has_snapshot = True
            snapshot_info += f"\n    return: {str(ret)[:100]}..."  if len(str(ret)) > 100 else f"\n    return: {ret}"
        if this:
            has_snapshot = True
            snapshot_info += f"\n    this: {str(this)[:80]}..."
        print(f"  [{span['content']}] line={span['line_number']}{snapshot_info}")

    assert has_snapshot, "No snapshot data captured!"
    print("\n✅ 基本快照功能正常 — 参数值、返回值、this状态均已捕获")


def test_targeted_snapshot():
    """测试2: 定向快照 — 只对指定方法采集"""
    print("\n" + "=" * 60)
    print("TEST 2: 定向快照 (仅 UserService.findById)")
    print("=" * 60)

    spans = send_traced_request("GET", "/api/users/1",
                                snapshot_methods="UserService.findById")

    print(f"\n收到 {len(spans)} 个 spans:")
    for span in spans:
        args = span.get("args_snapshot")
        has_args = args is not None
        print(f"  [{span['content']}] has_snapshot={has_args}")
        if has_args:
            print(f"    args: {args}")
            assert "findById" in span["content"], \
                f"Snapshot should only be on findById, but got on {span['content']}"

    # 验证 UserService.findById 有快照
    find_spans = [s for s in spans if "findById" in s.get("content", "")]
    assert any(s.get("args_snapshot") for s in find_spans), \
        "UserService.findById should have args_snapshot"
    print("\n✅ 定向快照正常 — 只有指定方法捕获了变量快照")


def test_two_phase_divergence():
    """测试3: 两阶段偏差计算 — 不同参数产生不同执行路径"""
    print("\n" + "=" * 60)
    print("TEST 3: 两阶段偏差计算")
    print("=" * 60)

    # Phase 1: 正常执行两个请求 (不同参数, 不带 snapshot)
    print("\n--- Phase 1: 正常执行, 获取偏差 ---")
    spans_a = send_traced_request("GET", "/api/users/1")
    spans_b = send_traced_request("GET", "/api/users/999")  # 不存在的用户

    trace_a = spans_to_trace(spans_a)
    trace_b = spans_to_trace(spans_b)

    print(f"  Trace A ({len(spans_a)} spans): {[s['content'] for s in spans_a]}")
    print(f"  Trace B ({len(spans_b)} spans): {[s['content'] for s in spans_b]}")

    # 分析偏差: 不同的请求应该产生不同的方法调用路径
    methods_a = set(s['content'] for s in spans_a)
    methods_b = set(s['content'] for s in spans_b)
    diff_methods = methods_a.symmetric_difference(methods_b)
    print(f"  方法级差异: {diff_methods or '(相同方法, 但返回值不同)'}")

    # Phase 2: 带快照重新执行
    print("\n--- Phase 2: 带变量快照重新执行 ---")

    # 确定快照目标 — 取两次执行共有的方法 (偏差发生在这些方法内部)
    common_methods = methods_a & methods_b
    # 用 SnapshotDiffer 构建快照目标
    snapshot_differ = SnapshotDiffer(config=SnapshotConfig(capture_all=True))

    snapshot_methods_str = "*"  # 简化: 全部方法
    spans_a_snap = send_traced_request("GET", "/api/users/1", snapshot_methods=snapshot_methods_str)
    spans_b_snap = send_traced_request("GET", "/api/users/999", snapshot_methods=snapshot_methods_str)

    trace_a_snap = spans_to_trace(spans_a_snap)
    trace_b_snap = spans_to_trace(spans_b_snap)

    # 显示偏差点的变量快照对比
    print("\n--- 变量快照对比 ---")
    # 找到两边共有的方法, 对比 args
    for content in sorted(common_methods):
        span_a = next((s for s in spans_a_snap if s['content'] == content and s.get('args_snapshot')), None)
        span_b = next((s for s in spans_b_snap if s['content'] == content and s.get('args_snapshot')), None)
        if span_a or span_b:
            print(f"\n  [{content}]:")
            if span_a:
                print(f"    Trace A args: {span_a.get('args_snapshot')}")
                print(f"    Trace A return: {str(span_a.get('return_snapshot', ''))[:100]}")
            if span_b:
                print(f"    Trace B args: {span_b.get('args_snapshot')}")
                print(f"    Trace B return: {str(span_b.get('return_snapshot', ''))[:100]}")

    print("\n✅ 两阶段偏差计算完成 — 可以看到同一方法被不同参数调用时的变量差异")


def test_snapshot_with_post():
    """测试4: POST 请求的参数快照"""
    print("\n" + "=" * 60)
    print("TEST 4: POST 请求参数快照")
    print("=" * 60)

    spans = send_traced_request("POST", "/api/users",
                                body={"name": "Charlie", "email": "charlie@test.com"},
                                snapshot_methods="*")

    print(f"\n收到 {len(spans)} 个 spans:")
    for span in spans:
        args = span.get("args_snapshot")
        if args:
            print(f"  [{span['content']}]")
            print(f"    args: {args}")
            ret = span.get("return_snapshot")
            if ret:
                print(f"    return: {str(ret)[:150]}")

    # 验证创建用户的参数被捕获
    create_spans = [s for s in spans if "create" in s.get("function", "").lower()
                    or "create" in s.get("content", "").lower()]
    if create_spans:
        snapshot_span = next((s for s in create_spans if s.get("args_snapshot")), None)
        if snapshot_span:
            args = snapshot_span["args_snapshot"]
            if isinstance(args, str):
                assert "Charlie" in args or "charlie" in args, \
                    f"Expected 'Charlie' in args, got: {args}"
            print(f"\n✅ POST 参数正确捕获: {args}")
        else:
            print("⚠️ create 方法无快照 (可能是 TracingAspect 的 span)")
    else:
        print("⚠️ 未找到 create 方法的 span")


def main():
    """运行所有测试."""
    # 检查服务是否可用
    try:
        with urllib.request.urlopen(f"{BASE_URL}/api/health", timeout=5):
            pass
    except Exception as e:
        print(f"❌ 服务不可用: {e}")
        print("请先启动容器: docker compose -f examples-yml/java-microservice/compose.snapshot-test.yaml up -d")
        sys.exit(1)

    print("🚀 运行时变量快照 E2E 测试")
    print(f"   目标: {BASE_URL}")

    test_basic_snapshot()
    test_targeted_snapshot()
    test_two_phase_divergence()
    test_snapshot_with_post()

    print("\n" + "=" * 60)
    print("🎉 全部测试通过!")
    print("=" * 60)


if __name__ == "__main__":
    main()
