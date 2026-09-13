"""
demo_fuzz_novel.py — novel-cloud (book-service) Log Injection Fuzz 攻击演示

目标: novel-cloud book-service
攻击面:
  1. GET /api/front/book/content/{chapterId} — chapterId 异常值触发 BusinessException
     → CommonExceptionHandler.handlerBusinessException → log.error(e.getMessage(), e)
  2. GET /api/front/book/{id} — 无效 ID 触发异常链
  3. 任意端点 + Authorization header 含恶意 JWT → JwtUtils.parseToken → log.warn("JWT解析失败:{}", token)

攻击成功条件:
  - trace 到达目标 sink 方法
  - 容器日志中出现攻击标记字符串

运行前提:
  cd examples-yml/novel-cloud && docker compose -f compose.real.yaml up -d

使用方式:
  set -a && source .env && set +a
  uv run python demo_fuzz_novel.py --max-attempts 10
  uv run python demo_fuzz_novel.py --verbose
  uv run python demo_fuzz_novel.py --mock
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
# novel-cloud book-service 配置
# ═══════════════════════════════════════════════════════════════════════════════

BASE_URL = "http://localhost:8080"
CONTAINER_NAME = "trace-real-novel-cloud-novel-book-service"
ATTACK_MARKER = "sink_attacked"

# ── API 入口 ──────────────────────────────────────────────────────────────────

API_GET_BOOK_CONTENT = APIEntry(
    class_name="io.github.xxyopen.novel.book.controller.front.FrontBookController",
    method="getBookContentAbout",
    http_method="GET",
    http_path="/api/front/book/content/{chapterId}",
    src_file="FrontBookController.java",
    line_number=0,
)

API_GET_BOOK_BY_ID = APIEntry(
    class_name="io.github.xxyopen.novel.book.controller.front.FrontBookController",
    method="getBookById",
    http_method="GET",
    http_path="/api/front/book/{id}",
    src_file="FrontBookController.java",
    line_number=0,
)

API_ADD_VISIT = APIEntry(
    class_name="io.github.xxyopen.novel.book.controller.front.FrontBookController",
    method="addVisitCount",
    http_method="POST",
    http_path="/api/front/book/visit",
    src_file="FrontBookController.java",
    line_number=0,
)

# ── Sink 定义 ─────────────────────────────────────────────────────────────────
# 攻击链 1: getBookContentAbout
#   chapterId (Long) 异常值 → BookServiceImpl.getBookContentAbout 触发 NPE/BusinessException
#   → CommonExceptionHandler.handlerException → log.error(e.getMessage(), e)
#   注意: @ExceptionHandler 不在 trace 中, sink 定义为 BookServiceImpl 方法
#
# 攻击链 2: JwtUtils.parseToken
#   Authorization header 中包含恶意 JWT 字符串 → JwtUtils.parseToken
#   → log.warn("JWT解析失败:{}", token) — 直接将 token 字符串打入日志

SINK_BOOK_CONTENT = Sink(
    class_name="io.github.xxyopen.novel.book.service.impl.BookServiceImpl",
    method="getBookContentAbout",
    sink_type=SinkType.LOG_ERROR,
    log_message="log.error(e.getMessage(), e)",
    tainted_params=["chapterId"],
    src_file="BookServiceImpl.java",
    line_number=455,
)

SINK_BOOK_BY_ID = Sink(
    class_name="io.github.xxyopen.novel.book.service.impl.BookServiceImpl",
    method="getBookById",
    sink_type=SinkType.LOG_ERROR,
    log_message="log.error(e.getMessage(), e)",
    tainted_params=["id"],
    src_file="BookServiceImpl.java",
    line_number=0,
)

SINK_VISIT = Sink(
    class_name="io.github.xxyopen.novel.book.service.impl.BookServiceImpl",
    method="addVisitCount",
    sink_type=SinkType.LOG_ERROR,
    log_message="log.error(e.getMessage(), e)",
    tainted_params=["bookId"],
    src_file="BookServiceImpl.java",
    line_number=0,
)

# ── 预期路径 (来自自动化静态分析 + 接口桥接) ──────────────────────────────────

PATH_BOOK_CONTENT = ExpectedPath(
    api_entry=API_GET_BOOK_CONTENT,
    log_sink=SINK_BOOK_CONTENT,
    nodes=[
        PathNode(
            class_name="io.github.xxyopen.novel.book.controller.front.FrontBookController",
            method="getBookContentAbout",
        ),
        PathNode(
            class_name="io.github.xxyopen.novel.book.service.impl.BookServiceImpl",
            method="getBookContentAbout",
        ),
    ],
    source=PathSource.CALL_GRAPH,
    confidence=0.5,
)

PATH_BOOK_BY_ID = ExpectedPath(
    api_entry=API_GET_BOOK_BY_ID,
    log_sink=SINK_BOOK_BY_ID,
    nodes=[
        PathNode(
            class_name="io.github.xxyopen.novel.book.controller.front.FrontBookController",
            method="getBookById",
        ),
        PathNode(
            class_name="io.github.xxyopen.novel.book.service.impl.BookServiceImpl",
            method="getBookById",
        ),
    ],
    source=PathSource.CALL_GRAPH,
    confidence=0.5,
)

PATH_VISIT = ExpectedPath(
    api_entry=API_ADD_VISIT,
    log_sink=SINK_VISIT,
    nodes=[
        PathNode(
            class_name="io.github.xxyopen.novel.book.controller.front.FrontBookController",
            method="addVisitCount",
        ),
        PathNode(
            class_name="io.github.xxyopen.novel.book.service.impl.BookServiceImpl",
            method="addVisitCount",
        ),
    ],
    source=PathSource.CALL_GRAPH,
    confidence=0.5,
)

# ── Fuzz 目标列表 ─────────────────────────────────────────────────────────────

FUZZ_TARGETS = [
    (API_GET_BOOK_CONTENT, SINK_BOOK_CONTENT, PATH_BOOK_CONTENT),
    (API_GET_BOOK_BY_ID, SINK_BOOK_BY_ID, PATH_BOOK_BY_ID),
    (API_ADD_VISIT, SINK_VISIT, PATH_VISIT),
]


# ═══════════════════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="novel-cloud Book Service Log Injection Fuzz Demo")
    parser.add_argument("--mock", action="store_true", help="使用 Mock LLM (不需要真实 LLM)")
    parser.add_argument("--base-url", default=BASE_URL, help="目标服务 URL")
    parser.add_argument("--max-attempts", type=int, default=10, help="每条路径最大尝试次数")
    parser.add_argument("--marker", default=ATTACK_MARKER, help="攻击标记字符串")
    parser.add_argument("--container", default=CONTAINER_NAME, help="目标 Docker 容器名称")
    parser.add_argument("--verbose", "-v", action="store_true", help="输出完整的 prompt/response 决策链")
    args = parser.parse_args()

    container_name = args.container

    def _check_log(marker: str, skip_lines: int) -> bool:
        return check_container_log_after_line(marker, skip_lines, container_name)

    def _get_log_line_count() -> int:
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
    source_root = "examples/novel-cloud"
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
    logger.info(f"目标项目: novel-cloud (book-service)")
    logger.info(f"Fuzz 目标数: {len(FUZZ_TARGETS)}")
    logger.info("")

    result = pipeline.run(FUZZ_TARGETS)

    # 输出结果
    print("\n" + result.summary)

    # 有攻击成功返回 0
    sys.exit(0 if result.reached_count > 0 else 1)


if __name__ == "__main__":
    main()
