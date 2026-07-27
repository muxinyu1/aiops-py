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

    trace_header = ""
    resp_body = ""
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp_body = resp.read().decode(errors="replace")
            trace_header = resp.getheader("X-Execution-Trace", "")
    except urllib.error.HTTPError as e:
        # 非 2xx 也可能有 trace
        resp_body = e.read().decode(errors="replace")
        trace_header = e.headers.get("X-Execution-Trace", "")
    except Exception:
        trace_header = ""

    # 解析 trace: 支持 header 模式和 IN_BODY 模式
    nodes: list[TraceNode] = []
    if trace_header == "IN_BODY":
        # Trace 数据太大放在了 body 中
        try:
            body_data = json.loads(resp_body)
            if "trace" in body_data:
                spans = body_data["trace"]
                nodes = _parse_spans(spans)
        except Exception as e:
            logger.debug(f"Trace IN_BODY 解析失败: {e}")
    elif trace_header:
        # 正常 Base64 header 模式
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
# Docker 日志检查
# ═══════════════════════════════════════════════════════════════════════════════

# 默认攻击标记
ATTACK_MARKER = "sink_attacked"

# 容器名称 (java-microservice 的容器)
CONTAINER_NAME = "trace-real-java-microservice"


def check_container_log(marker: str, container_name: str = CONTAINER_NAME, since: str = "") -> bool:
    """
    检查 Docker 容器**最近新增**的日志中是否出现攻击标记。

    策略: 使用 --since 时间戳只看请求发出之后的日志。
    额外过滤: 排除 Spring 框架自身的异常转换日志（MethodArgumentTypeMismatchException），
    只匹配应用代码自己的日志输出。

    Args:
        marker: 要检查的攻击标记字符串
        container_name: Docker 容器名称
        since: 只检查此时间戳之后的日志 (ISO 格式)

    Returns:
        bool: 应用日志中是否包含 marker（排除框架异常日志）
    """
    import subprocess
    try:
        cmd = ["docker", "logs"]
        if since:
            cmd += ["--since", since]
        else:
            cmd += ["--tail", "10"]
        cmd.append(container_name)

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=5,
        )
        # 同时检查 stdout 和 stderr
        output = result.stdout + result.stderr

        # 逐行检查: marker 必须出现在应用日志行中（排除 Spring 框架异常行）
        for line in output.splitlines():
            if marker not in line:
                continue
            # 排除 Spring 框架的类型转换异常日志
            if "MethodArgumentTypeMismatchException" in line:
                continue
            if "DefaultHandlerExceptionResolver" in line:
                continue
            if "Failed to convert value" in line:
                continue
            # 找到了应用代码产生的含 marker 的日志行
            return True

        return False
    except Exception:
        return False


def get_container_log_line_count(container_name: str = CONTAINER_NAME) -> int:
    """获取容器当前日志总行数."""
    import subprocess
    try:
        result = subprocess.run(
            ["docker", "logs", container_name],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=10,
        )
        return len(result.stdout.splitlines())
    except Exception:
        return 0


def check_container_log_after_line(
    marker: str,
    skip_lines: int,
    container_name: str = CONTAINER_NAME,
) -> bool:
    """
    检查容器日志中第 skip_lines 行之后的新增日志是否包含攻击标记。

    这是最可靠的方式 — 不依赖时间戳，完全基于行数差值。

    Args:
        marker: 要检查的攻击标记字符串
        skip_lines: 跳过前 N 行（请求前的日志行数）
        container_name: Docker 容器名称

    Returns:
        bool: 新增日志中是否包含 marker（排除框架异常行）
    """
    import subprocess
    try:
        result = subprocess.run(
            ["docker", "logs", container_name],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=10,
        )
        all_lines = result.stdout.splitlines()

        # 只取新增的行
        new_lines = all_lines[skip_lines:]

        for line in new_lines:
            if marker not in line:
                continue
            # 排除 Spring 框架的类型转换异常日志
            if "MethodArgumentTypeMismatchException" in line:
                continue
            if "DefaultHandlerExceptionResolver" in line:
                continue
            if "Failed to convert value" in line:
                continue
            if "TypeMismatchException" in line:
                continue
            # 找到了应用代码产生的含 marker 的日志行
            return True

        return False
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Log Injection Fuzz Pipeline Demo")
    parser.add_argument("--mock", action="store_true", help="使用 Mock LLM (不需要真实 LLM)")
    parser.add_argument("--base-url", default=BASE_URL, help="目标服务 URL")
    parser.add_argument("--max-attempts", type=int, default=10, help="每条路径最大尝试次数")
    parser.add_argument("--marker", default=ATTACK_MARKER, help="攻击标记字符串")
    parser.add_argument("--container", default=CONTAINER_NAME, help="目标 Docker 容器名称")
    parser.add_argument("--verbose", "-v", action="store_true", help="输出完整的 prompt/response 决策链")
    args = parser.parse_args()

    # 用闭包捕获容器名
    container_name = args.container

    def _check_log(marker: str, skip_lines: int) -> bool:
        """检查容器新增日志中是否出现攻击标记（基于行数差值）."""
        return check_container_log_after_line(marker, skip_lines, container_name)

    def _get_log_line_count() -> int:
        """获取容器当前日志行数."""
        return get_container_log_line_count(container_name)

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
    source_root = "examples/java-microservice/src/main/java"
    fuzzer = Fuzzer(
        llm=llm,
        base_url=args.base_url,
        attack_marker=args.marker,
        source_root=source_root,
        verbose=args.verbose,
    )
    pipeline = Pipeline(
        fuzzer=fuzzer,
        execute_fn=execute_with_trace,
        check_log_fn=_check_log,
        get_log_line_count_fn=_get_log_line_count,
        max_attempts=args.max_attempts,
    )

    # 运行攻击
    logger.info(f"目标服务: {args.base_url}")
    logger.info(f"攻击标记: \"{args.marker}\"")
    logger.info(f"目标容器: {args.container}")
    logger.info(f"Fuzz 目标数: {len(FUZZ_TARGETS)}")
    logger.info("")

    result = pipeline.run(FUZZ_TARGETS)

    # 输出结果
    print("\n" + result.summary)

    # 返回码: 有攻击成功的路径则返回 0
    sys.exit(0 if result.reached_count > 0 else 1)


if __name__ == "__main__":
    main()
