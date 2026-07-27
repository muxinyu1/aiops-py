"""
demo_fuzz_piggy.py — PiggyMetrics Fuzz Pipeline 真实项目演示

目标: PiggyMetrics account-service (Spring Cloud 微服务, 有 OAuth2 保护)
挑战:
  1. POST / (createNewAccount) 不需要认证, 但内部调用 authClient 会失败 (auth-service 不可用)
  2. 要触发 ErrorHandler.processValidationError, 需要重复创建已存在的用户名
     → Assert.isNull 失败 → IllegalArgumentException → ErrorHandler → log.info("400 Bad Request")
  3. GET /{name} 有 @PreAuthorize 但允许 name='demo' 访问 (硬编码)
  4. GET /current, PUT /current 需要 OAuth2 认证

这个场景比 java-microservice 更复杂:
  - LLM 需要理解 "重复创建用户" 才能触发特定错误分支
  - trace 可能通过 IN_BODY 模式返回 (数据量大时)
  - 多层调用: Controller → ServiceImpl → Repository → ErrorHandler

运行前提:
  cd examples-yml/PiggyMetrics && docker compose -f compose.real.yaml up -d
  # 在 MongoDB 中预置一个用户:
  docker exec trace-real-mongo mongo --quiet --eval \
    'db.getSiblingDB("test").accounts.insertOne({"_id":"demo","name":"demo","lastSeen":new Date()})'

使用方式:
  source .env && export LLM_BASE_URL LLM_API_KEY LLM_MODEL
  uv run python demo_fuzz_piggy.py --max-attempts 8
  uv run python demo_fuzz_piggy.py --mock  # 无 LLM 测试
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from demo_fuzz import execute_with_trace, _parse_spans, MockLLM
from expected_path import APIEntry, ExpectedPath, PathNode, PathSource
from fuzzer import Fuzzer
from llm import LLM, Message
from parameter import HttpParameter
from pipeline import Pipeline, PipelineResult, check_sink_reached
from sink import Sink, SinkType

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# PiggyMetrics account-service 手工标注
# ═══════════════════════════════════════════════════════════════════════════════

BASE_URL = "http://localhost:8080"

# ── API 入口 ──────────────────────────────────────────────────────────────────

API_CREATE_ACCOUNT = APIEntry(
    class_name="com.piggymetrics.account.controller.AccountController",
    method="createNewAccount",
    http_method="POST",
    http_path="/",
    src_file="AccountController.java",
    line_number=37,
)

API_GET_ACCOUNT_BY_NAME = APIEntry(
    class_name="com.piggymetrics.account.controller.AccountController",
    method="getAccountByName",
    http_method="GET",
    http_path="/{name}",
    src_file="AccountController.java",
    line_number=21,
)

API_SAVE_CURRENT = APIEntry(
    class_name="com.piggymetrics.account.controller.AccountController",
    method="saveCurrentAccount",
    http_method="PUT",
    http_path="/current",
    src_file="AccountController.java",
    line_number=31,
)

# ── Sink: 日志/异常处理点 ────────────────────────────────────────────────────

# Sink 1: ErrorHandler 捕获 IllegalArgumentException → log.info("400 Bad Request")
SINK_ERROR_HANDLER_400 = Sink(
    class_name="com.piggymetrics.account.controller.ErrorHandler",
    method="processValidationError",
    line_number=20,
    src_file="ErrorHandler.java",
    sink_type=SinkType.LOG_INFO,
    log_message="Returning HTTP 400 Bad Request",
    log_api="log.info",
    tainted_params=["username"],
)

# Sink 2: AccountServiceImpl.create 成功 → log.info("new account has been created")
SINK_ACCOUNT_CREATED = Sink(
    class_name="com.piggymetrics.account.service.AccountServiceImpl",
    method="create",
    line_number=67,
    src_file="AccountServiceImpl.java",
    sink_type=SinkType.LOG_INFO,
    log_message="new account has been created: {}",
    log_api="log.info",
    tainted_params=["username"],
)

# Sink 3: AccountServiceImpl.saveChanges → log.debug("account changes saved")
SINK_ACCOUNT_SAVED = Sink(
    class_name="com.piggymetrics.account.service.AccountServiceImpl",
    method="saveChanges",
    line_number=88,
    src_file="AccountServiceImpl.java",
    sink_type=SinkType.LOG_DEBUG,
    log_message="account {} changes has been saved",
    log_api="log.debug",
    tainted_params=["name"],
)

# ── 预期路径 ──────────────────────────────────────────────────────────────────

# Path 1: 重复创建已存在用户 → ErrorHandler (3 层深度, 有数据依赖)
PATH_DUPLICATE_USER_ERROR = ExpectedPath(
    api_entry=API_CREATE_ACCOUNT,
    log_sink=SINK_ERROR_HANDLER_400,
    nodes=[
        PathNode(
            class_name="com.piggymetrics.account.controller.AccountController",
            method="createNewAccount", depth=0),
        PathNode(
            class_name="com.piggymetrics.account.service.AccountServiceImpl",
            method="create", depth=1),
        PathNode(
            class_name="com.piggymetrics.account.controller.ErrorHandler",
            method="processValidationError", depth=0),  # @ControllerAdvice, 独立调用链
    ],
    source=PathSource.TAINT,
    confidence=0.7,
)

# Path 2: 创建新用户 → 但 auth-service 不可用 → create 抛 OAuth2 异常 (不走 ErrorHandler)
# 这条路径实际上会失败 — 因为 authClient 连不上, 但 create 方法会被执行
PATH_CREATE_NEW_USER = ExpectedPath(
    api_entry=API_CREATE_ACCOUNT,
    log_sink=None,
    nodes=[
        PathNode(
            class_name="com.piggymetrics.account.controller.AccountController",
            method="createNewAccount", depth=0),
        PathNode(
            class_name="com.piggymetrics.account.service.AccountServiceImpl",
            method="create", depth=1),
    ],
    source=PathSource.TAINT,
    confidence=0.6,
)

# Path 3: 通过 @PreAuthorize 条件绕过认证查询账户 (name='demo')
PATH_GET_ACCOUNT_BYPASS = ExpectedPath(
    api_entry=API_GET_ACCOUNT_BY_NAME,
    log_sink=None,
    nodes=[
        PathNode(
            class_name="com.piggymetrics.account.controller.AccountController",
            method="getAccountByName", depth=0),
        PathNode(
            class_name="com.piggymetrics.account.service.AccountServiceImpl",
            method="findByName", depth=1),
    ],
    source=PathSource.TAINT,
    confidence=0.8,
)


# ── Fuzz 目标清单 ─────────────────────────────────────────────────────────────

FUZZ_TARGETS = [
    # 核心挑战: 触发 ErrorHandler (需要重复创建已存在用户)
    (API_CREATE_ACCOUNT, SINK_ERROR_HANDLER_400, PATH_DUPLICATE_USER_ERROR),
    # 次要: 新用户创建 (会因 auth-service 不可用而 500, 但 create 方法被执行)
    (API_CREATE_ACCOUNT, SINK_ACCOUNT_CREATED, PATH_CREATE_NEW_USER),
    # 容易: 绕过 PreAuthorize 查询 (name='demo' 硬编码允许)
    (API_GET_ACCOUNT_BY_NAME, Sink(class_name="com.piggymetrics.account.service.AccountServiceImpl",
                                    method="findByName"),
     PATH_GET_ACCOUNT_BYPASS),
]


# ═══════════════════════════════════════════════════════════════════════════════
# PiggyMetrics 专用 Mock LLM
# ═══════════════════════════════════════════════════════════════════════════════

class PiggyMockLLM(LLM):
    """
    Mock LLM: 模拟多轮 fuzz 的推理行为。

    场景 1 (ErrorHandler): 第 1 次用新用户名 → 500, 第 2 次用 'demo' → 400
    场景 2 (create): 用全新用户名创建
    场景 3 (getAccountByName): 直接用 /demo 绕过认证
    """

    def __init__(self):
        super().__init__(base_url="mock://", api_key="mock", model="mock")
        self._call_count = 0

    def chat(self, messages: list[Message]) -> str:
        self._call_count += 1
        system = messages[0].content if messages else ""

        if "processValidationError" in system or "ErrorHandler" in system:
            # 场景 1: 需要触发重复用户错误
            if self._call_count <= 1:
                # 第 1 次: 用新用户名尝试 (会因 auth-service 报 500)
                return json.dumps({
                    "method": "POST",
                    "url": f"{BASE_URL}/",
                    "headers": {"Content-Type": "application/json"},
                    "body": {"username": "newuser123", "password": "pass123"},
                })
            else:
                # 第 2 次: 用已存在的 'demo' → Assert.isNull 失败 → 400
                return json.dumps({
                    "method": "POST",
                    "url": f"{BASE_URL}/",
                    "headers": {"Content-Type": "application/json"},
                    "body": {"username": "demo", "password": "pass123"},
                })

        elif "createNewAccount" in system and "create" in system:
            # 场景 2: 创建新用户 (会触发 create 但 auth-client 失败)
            return json.dumps({
                "method": "POST",
                "url": f"{BASE_URL}/",
                "headers": {"Content-Type": "application/json"},
                "body": {"username": f"fuzzuser_{self._call_count}", "password": "test123"},
            })

        elif "getAccountByName" in system or "/{name}" in system:
            # 场景 3: 用 demo 绕过 PreAuthorize
            return json.dumps({
                "method": "GET",
                "url": f"{BASE_URL}/demo",
                "headers": {},
                "body": None,
            })

        # fallback
        return json.dumps({
            "method": "GET",
            "url": f"{BASE_URL}/demo",
            "headers": {},
            "body": None,
        })


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="PiggyMetrics Fuzz Pipeline")
    parser.add_argument("--mock", action="store_true", help="使用 MockLLM (不需要真实 LLM)")
    parser.add_argument("--base-url", default=BASE_URL, help="目标服务 URL")
    parser.add_argument("--max-attempts", type=int, default=8, help="每条路径最大尝试次数")
    args = parser.parse_args()

    global BASE_URL
    BASE_URL = args.base_url

    # 初始化 LLM
    if args.mock:
        llm = PiggyMockLLM()
        logger.info("使用 MockLLM (测试模式)")
    else:
        import os
        llm = LLM(
            base_url=os.environ.get("LLM_BASE_URL", ""),
            api_key=os.environ.get("LLM_API_KEY", ""),
            model=os.environ.get("LLM_MODEL", "gpt-4o"),
        )
        logger.info(f"使用 LLM: {llm.model} @ {llm.base_url}")

    logger.info(f"目标服务: {BASE_URL}")
    logger.info(f"Fuzz 目标数: {len(FUZZ_TARGETS)}")
    logger.info("")

    # 构建 Pipeline
    fuzzer = Fuzzer(llm=llm, base_url=BASE_URL)
    pipeline = Pipeline(
        fuzzer=fuzzer,
        execute_fn=execute_with_trace,
        max_attempts=args.max_attempts,
    )

    # 运行
    result = pipeline.run(FUZZ_TARGETS)

    # 输出结果
    logger.info(f"\n{result.summary}")
    print(f"\n{result.summary}")

    # 返回码
    sys.exit(0 if result.reached_count > 0 else 1)


if __name__ == "__main__":
    main()
