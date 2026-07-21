"""
demo_fuzz.py — Fuzz Pipeline 端到端演示

使用 java-microservice 项目作为目标，手工标注几个 LogSink，
演示完整的 fuzz 循环:
  Fuzzer 生成参数 → 执行请求 → 检查 sink → 偏差反馈 → 迭代

运行前提:
  1. java-microservice 容器已启动 (docker compose up)
  2. 设置环境变量: LLM_BASE_URL, LLM_API_KEY, LLM_MODEL

使用方式:
  # 使用 Docker compose 启动目标服务
  cd examples-yml/java-microservice && docker compose -f compose.real.yaml up -d

  # 设置 LLM 配置
  export LLM_BASE_URL="https://api.openai.com/v1"
  export LLM_API_KEY="sk-..."
  export LLM_MODEL="gpt-4o"

  # 运行 fuzz
  uv run python demo_fuzz.py

  # 或使用 mock 模式（不需要 LLM，用于测试框架）
  uv run python demo_fuzz.py --mock
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass

from expected_path import APIEntry, ExpectedPath, PathNode, PathSource
from fuzzer import Fuzzer, FuzzAttempt
from llm import LLM, Message
from parameter import HttpParameter
from path_differ import PathDiffer
from pipeline import Pipeline, PipelineResult, check_sink_reached
from sink import Sink, SinkType
from source import Source, Type, RESTfulSource
from trace import Trace, TraceNode

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# java-microservice 项目的手工标注
# ═══════════════════════════════════════════════════════════════════════════════

# 目标服务基础 URL
BASE_URL = "http://localhost:8080"

# ── API 入口 ──────────────────────────────────────────────────────────────────
API_GET_USER = APIEntry(
    class_name="com.example.microservice.controller.AppController",
    method="getUser",
    http_method="GET",
    http_path="/api/users/{id}",
    src_file="AppController.java",
    line_number=37,
)

API_CREATE_USER = APIEntry(
    class_name="com.example.microservice.controller.AppController",
    method="createUser",
    http_method="POST",
    http_path="/api/users",
    src_file="AppController.java",
    line_number=50,
)

API_CANCEL_ORDER = APIEntry(
    class_name="com.example.microservice.controller.AppController",
    method="cancelOrder",
    http_method="DELETE",
    http_path="/api/orders/{id}",
    src_file="AppController.java",
    line_number=97,
)

# ── LogSink 日志打印点 ────────────────────────────────────────────────────────
SINK_USER_NOT_FOUND = Sink(
    class_name="com.example.microservice.controller.AppController",
    method="getUser",
    line_number=43,
    src_file="AppController.java",
    sink_type=SinkType.LOG_WARN,
    log_message="User not found: id={}",
    log_api="log.warn",
    tainted_params=["id"],
)

SINK_CREATE_USER_FAILED = Sink(
    class_name="com.example.microservice.controller.AppController",
    method="createUser",
    line_number=60,
    src_file="AppController.java",
    sink_type=SinkType.LOG_ERROR,
    log_message="Failed to create user: {}",
    log_api="log.error",
    tainted_params=["name", "email"],
)

SINK_CANCEL_ORDER_NOT_FOUND = Sink(
    class_name="com.example.microservice.controller.AppController",
    method="cancelOrder",
    line_number=105,
    src_file="AppController.java",
    sink_type=SinkType.LOG_WARN,
    log_message="Cancel failed, order not found: id={}",
    log_api="log.warn",
    tainted_params=["id"],
)

# ── 预期路径 ──────────────────────────────────────────────────────────────────

PATH_GET_USER_NOT_FOUND = ExpectedPath(
    api_entry=API_GET_USER,
    log_sink=None,  # type: ignore
    nodes=[
        PathNode(class_name="com.example.microservice.controller.AppController",
                 method="getUser", depth=0),
        PathNode(class_name="com.example.microservice.service.UserService",
                 method="findById", depth=1),
    ],
    source=PathSource.TAINT,
    confidence=0.8,
)

PATH_CREATE_USER_INVALID = ExpectedPath(
    api_entry=API_CREATE_USER,
    log_sink=None,  # type: ignore
    nodes=[
        PathNode(class_name="com.example.microservice.controller.AppController",
                 method="createUser", depth=0),
        PathNode(class_name="com.example.microservice.service.UserService",
                 method="create", depth=1),
    ],
    source=PathSource.TAINT,
    confidence=0.8,
)

PATH_CANCEL_ORDER_NOT_FOUND = ExpectedPath(
    api_entry=API_CANCEL_ORDER,
    log_sink=None,  # type: ignore
    nodes=[
        PathNode(class_name="com.example.microservice.controller.AppController",
                 method="cancelOrder", depth=0),
        PathNode(class_name="com.example.microservice.service.OrderService",
                 method="cancel", depth=1),
    ],
    source=PathSource.TAINT,
    confidence=0.8,
)

# ── Fuzz 目标清单 ─────────────────────────────────────────────────────────────

FUZZ_TARGETS = [
    (API_GET_USER, SINK_USER_NOT_FOUND, PATH_GET_USER_NOT_FOUND),
    (API_CREATE_USER, SINK_CREATE_USER_FAILED, PATH_CREATE_USER_INVALID),
    (API_CANCEL_ORDER, SINK_CANCEL_ORDER_NOT_FOUND, PATH_CANCEL_ORDER_NOT_FOUND),
]


# ═══════════════════════════════════════════════════════════════════════════════
# 执行器：发送 HTTP 请求并解析 X-Execution-Trace
# ═══════════════════════════════════════════════════════════════════════════════

def execute_with_trace(param: HttpParameter) -> Trace:
    """
    发送带 X-Return-Trace 头的请求，解析响应中的 trace 数据。

    这是 Pipeline 需要的 execute_fn 实现。
    """
    headers = {**param.headers, "X-Return-Trace": "true"}
    if param.body is not None:
        headers.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(
        url=param.url,
        method=param.method,
        headers=headers,
        data=param.body.encode() if param.body else None,
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
            trace_header = resp.getheader("X-Execution-Trace", "")
    except urllib.error.HTTPError as e:
        # 非 2xx 也可能有 trace
        trace_header = e.headers.get("X-Execution-Trace", "")
    except Exception:
        trace_header = ""

    # 解析 X-Execution-Trace (Base64 encoded JSON span array)
    nodes: list[TraceNode] = []
    if trace_header:
        try:
            decoded = base64.b64decode(trace_header)
            spans = json.loads(decoded)
            nodes = _parse_spans(spans)
        except Exception as e:
            logger.debug(f"Trace 解析失败: {e}")

    return Trace(
        source=Source(type=Type.RESTFUL, data=RESTfulSource()),
        sink=Sink(class_name="", method=""),
        nodes=nodes,
    )


def _parse_spans(spans: list[dict]) -> list[TraceNode]:
    """将 X-Execution-Trace 的 span JSON 数组解析为 TraceNode 列表."""
    nodes: list[TraceNode] = []
    for span in spans:
        nodes.append(TraceNode(
            span_id=span.get("span_id", ""),
            parent_span_id=span.get("parent_span_id", ""),
            trace_id=span.get("trace_id", ""),
            content=span.get("content", ""),
            function=span.get("function", ""),
            method_signature=span.get("method_signature", ""),
            class_namespace=span.get("class_namespace", ""),
            src_file=span.get("src_file", ""),
            line_number=span.get("line_number", -1),
            start_ns=span.get("start_ns", 0),
            duration_ns=span.get("duration_ns", 0),
            is_error=span.get("is_error", False),
            error_message=span.get("error_message"),
        ))
    return nodes


# ═══════════════════════════════════════════════════════════════════════════════
# Mock LLM（测试用，不需要真实 LLM）
# ═══════════════════════════════════════════════════════════════════════════════

class MockLLM(LLM):
    """
    Mock LLM 用于测试框架本身。

    对不同 API 返回合理的 fuzz 请求，模拟 LLM 的推理行为。
    """

    def __init__(self):
        super().__init__(base_url="mock://", api_key="mock", model="mock")
        self._call_count = 0

    def chat(self, messages: list[Message]) -> str:
        self._call_count += 1
        system = messages[0].content if messages else ""

        # 根据 system prompt 中的 API 信息决定返回什么
        if "/api/users/{id}" in system:
            # GET user — 用一个不存在的 id 触发 user not found
            return json.dumps({
                "method": "GET",
                "url": f"{BASE_URL}/api/users/99999",
                "headers": {},
                "body": None,
            })
        elif "POST" in system and "/api/users" in system:
            # POST user — 用无效 email 触发错误
            return json.dumps({
                "method": "POST",
                "url": f"{BASE_URL}/api/users",
                "headers": {"Content-Type": "application/json"},
                "body": {"name": "test", "email": "invalid-email"},
            })
        elif "/api/orders/{id}" in system:
            # DELETE order — 用一个不存在的 id 触发 order not found
            return json.dumps({
                "method": "DELETE",
                "url": f"{BASE_URL}/api/orders/99999",
                "headers": {},
                "body": None,
            })
        else:
            return json.dumps({
                "method": "GET",
                "url": f"{BASE_URL}/api/health",
                "headers": {},
                "body": None,
            })


# ═══════════════════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Sink-Centric Fuzz Pipeline Demo")
    parser.add_argument("--mock", action="store_true", help="使用 Mock LLM (不需要真实 LLM)")
    parser.add_argument("--base-url", default=BASE_URL, help="目标服务 URL")
    parser.add_argument("--max-attempts", type=int, default=5, help="每条路径最大尝试次数")
    args = parser.parse_args()

    # 选择 LLM
    if args.mock:
        logger.info("使用 Mock LLM 模式")
        llm = MockLLM()
    else:
        llm = LLM()
        if not llm.api_key:
            logger.error("未设置 LLM_API_KEY 环境变量，请设置或使用 --mock 模式")
            sys.exit(1)
        logger.info(f"使用 LLM: {llm.model} @ {llm.base_url}")

    # 构建 pipeline
    fuzzer = Fuzzer(llm=llm, base_url=args.base_url)
    pipeline = Pipeline(
        fuzzer=fuzzer,
        execute_fn=execute_with_trace,
        max_attempts=args.max_attempts,
    )

    # 运行 fuzz
    logger.info(f"目标服务: {args.base_url}")
    logger.info(f"Fuzz 目标数: {len(FUZZ_TARGETS)}")
    logger.info("")

    result = pipeline.run(FUZZ_TARGETS)

    # 输出结果
    print("\n" + result.summary)

    # 返回码: 有成功到达 sink 的路径则返回 0
    sys.exit(0 if result.reached_count > 0 else 1)


if __name__ == "__main__":
    main()
