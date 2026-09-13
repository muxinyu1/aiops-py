"""
demo_fuzz_ruoyi.py — RuoYi-Cloud (ruoyi-auth) Log Injection Fuzz 攻击演示

目标: RuoYi-Cloud ruoyi-auth 服务
攻击面:
  1. POST /login — username 流入 Feign 调用 URL → 503 异常 → GlobalExceptionHandler.handleServiceException → log.error(e.getMessage(), e)
  2. POST /register — 同上路径

攻击成功条件:
  - trace 到达目标 sink 方法
  - 容器日志中出现攻击标记字符串

运行前提:
  cd examples-yml/RuoYi-Cloud && docker compose -f compose.real.yaml up -d

使用方式:
  source .env && export LLM_BASE_URL LLM_API_KEY LLM_MODEL
  uv run python demo_fuzz_ruoyi.py --max-attempts 5
  uv run python demo_fuzz_ruoyi.py --verbose  # 输出完整 LLM 决策链
  uv run python demo_fuzz_ruoyi.py --mock     # 无需 LLM 的测试模式
"""

from __future__ import annotations

import argparse
import logging
import sys

from demo_fuzz import (
    execute_with_trace,
    check_container_log_after_line,
    get_container_log_line_count,
    MockLLM,
)
from expected_path import APIEntry, ExpectedPath, PathNode, PathSource
from fuzzer import Fuzzer
from llm import LLM
from pipeline import Pipeline
from sink import Sink, SinkType

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# RuoYi-Cloud ruoyi-auth 手工标注
# ═══════════════════════════════════════════════════════════════════════════════

BASE_URL = "http://localhost:8080"
CONTAINER_NAME = "trace-real-ruoyi-cloud-ruoyi-auth"
ATTACK_MARKER = "sink_attacked"

# ── API 入口 ──────────────────────────────────────────────────────────────────

API_LOGIN = APIEntry(
    class_name="com.ruoyi.auth.controller.TokenController",
    method="login",
    http_method="POST",
    http_path="/login",
    src_file="TokenController.java",
    line_number=39,
)

API_REGISTER = APIEntry(
    class_name="com.ruoyi.auth.controller.TokenController",
    method="register",
    http_method="POST",
    http_path="/register",
    src_file="TokenController.java",
    line_number=73,
)

# ── Sink 定义 ─────────────────────────────────────────────────────────────────
# 攻击链:
#   username 流入 Feign URL → ServiceUnavailable 异常 →
#   GlobalExceptionHandler.handleServiceException → log.error(e.getMessage(), e)
#   异常 stack trace 中包含 http://ruoyi-system/user/info/<username>
#
# 注意: GlobalExceptionHandler 是 @ExceptionHandler，不会出现在 TracingAspect trace 中
# 所以 sink 定义为 SysLoginService.login (实际出现在 trace 中的最后一个业务方法)

SINK_LOGIN = Sink(
    class_name="com.ruoyi.auth.service.SysLoginService",
    method="login",
    sink_type=SinkType.LOG_ERROR,
    log_message="[503] during [GET] to [http://ruoyi-system/user/info/{}]",
    tainted_params=["username"],
    src_file="SysLoginService.java",
    line_number=48,
)

SINK_REGISTER = Sink(
    class_name="com.ruoyi.auth.service.SysLoginService",
    method="register",
    sink_type=SinkType.LOG_ERROR,
    log_message="[503] during [POST] to [http://ruoyi-system/user/register/{}]",
    tainted_params=["username"],
    src_file="SysLoginService.java",
    line_number=153,
)

# ── 预期路径 ──────────────────────────────────────────────────────────────────

PATH_LOGIN = ExpectedPath(
    api_entry=API_LOGIN,
    log_sink=SINK_LOGIN,
    nodes=[
        PathNode(
            class_name="com.ruoyi.auth.controller.TokenController",
            method="login",
        ),
        PathNode(
            class_name="com.ruoyi.auth.service.SysLoginService",
            method="login",
        ),
    ],
    source=PathSource.TAINT,
    confidence=0.9,
)

PATH_REGISTER = ExpectedPath(
    api_entry=API_REGISTER,
    log_sink=SINK_REGISTER,
    nodes=[
        PathNode(
            class_name="com.ruoyi.auth.controller.TokenController",
            method="register",
        ),
        PathNode(
            class_name="com.ruoyi.auth.service.SysLoginService",
            method="register",
        ),
    ],
    source=PathSource.TAINT,
    confidence=0.9,
)

# ── Fuzz 目标列表 ─────────────────────────────────────────────────────────────

FUZZ_TARGETS = [
    (API_LOGIN, SINK_LOGIN, PATH_LOGIN),
    (API_REGISTER, SINK_REGISTER, PATH_REGISTER),
]


# ═══════════════════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="RuoYi-Cloud Log Injection Fuzz Demo")
    parser.add_argument("--mock", action="store_true", help="使用 Mock LLM (不需要真实 LLM)")
    parser.add_argument("--base-url", default=BASE_URL, help="目标服务 URL")
    parser.add_argument("--max-attempts", type=int, default=5, help="每条路径最大尝试次数")
    parser.add_argument("--marker", default=ATTACK_MARKER, help="攻击标记字符串")
    parser.add_argument("--container", default=CONTAINER_NAME, help="目标 Docker 容器名称")
    parser.add_argument("--verbose", "-v", action="store_true", help="输出完整的 prompt/response 决策链")
    args = parser.parse_args()

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
    source_root = "examples/RuoYi-Cloud"
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
    logger.info(f"目标容器: {container_name}")
    logger.info(f"目标项目: RuoYi-Cloud (ruoyi-auth)")
    logger.info(f"Fuzz 目标数: {len(FUZZ_TARGETS)}")
    logger.info("")

    result = pipeline.run(FUZZ_TARGETS)

    # 输出结果
    print("\n" + result.summary)

    # 返回码: 有攻击成功的路径则返回 0
    sys.exit(0 if result.reached_count > 0 else 1)


if __name__ == "__main__":
    main()
