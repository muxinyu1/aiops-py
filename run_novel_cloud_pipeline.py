#!/usr/bin/env python3
"""
run_novel_cloud_pipeline.py — novel-cloud 全自动化 Fuzz Pipeline

自动化步骤:
  1. Joern 生成调用图
  2. CodeQL 污点分析
  3. API 发现 + 预期路径生成 (利用 Sink JSON + taint CG)
  4. Fuzz 每条预期路径, 检查 marker

用法:
  uv run python run_novel_cloud_pipeline.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

# 项目路径
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from api_discovery import discover_api_entries
from codeql_adapter import CodeQLAdapter, CodeQLConfig
from demo_fuzz import execute_with_trace, check_container_log_after_line, get_container_log_line_count
from expected_path import APIEntry, ExpectedPath, PathNode, PathSource, LogSink
from fuzzer import Fuzzer
from joern_adapter import JoernAdapter, JoernConfig, load_call_graph_from_json
from llm import LLM
from path_generator import CallGraph, CallGraphNode, CallGraphEdge, PathGenerator
from pipeline import Pipeline
from sink import Sink, SinkType

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════════════════════

NOVEL_CLOUD_ROOT = str(PROJECT_ROOT / "examples" / "novel-cloud")
# book-service + core 是目标服务的源码
NOVEL_CLOUD_SOURCES = [
    str(PROJECT_ROOT / "examples" / "novel-cloud" / "novel-book"),
    str(PROJECT_ROOT / "examples" / "novel-cloud" / "novel-core"),
]

PACKAGE_FILTER = "io.github.xxyopen.novel"
PROJECT_NAME = "novel-cloud"

# 工具路径
JOERN_HOME = os.path.expanduser("~/bin/joern/joern-cli")
CODEQL_HOME = os.path.expanduser("~/bin/codeql")

# 缓存路径 (novel-cloud-callgraph.json 已存在于项目中)
JOERN_CG_CACHE = str(PROJECT_ROOT / "novel-cloud-callgraph.json")
CODEQL_DB_PATH = "/tmp/codeql-db-novel-cloud"
CODEQL_CSV_PATH = "/tmp/novel-cloud-taint-results.csv"

# Fuzz 配置
BASE_URL = "http://localhost:8080"
CONTAINER_NAME = "trace-real-novel-cloud-novel-book-service"
ATTACK_MARKER = "FUZZ_MARKER_7x9k"  # 足够独特的 marker
SINKS_JSON = str(PROJECT_ROOT / "novel-cloud-logging-sinks.json")


# ═══════════════════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════════════════

def _save_call_graph_json(cg: CallGraph, path: str) -> None:
    """将 CallGraph 序列化保存为 JSON (与 load_call_graph_from_json 兼容)."""
    methods = []
    for nid, node in cg.nodes.items():
        methods.append({
            "fullName": nid,
            "name": node.method,
            "signature": node.method_signature,
            "filename": node.src_file,
            "lineNumber": node.line_number,
            "className": node.class_name,
        })

    calls = []
    for edge in cg.edges:
        calls.append({
            "callerFullName": edge.caller_id,
            "calleeFullName": edge.callee_id,
            "lineNumber": edge.call_line,
        })

    data = {"methods": methods, "calls": calls}
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _add_interceptor_edges(cg: CallGraph) -> int:
    """
    添加 Spring 拦截器合成边.

    Spring MVC 拦截器 (HandlerInterceptor) 的调用由框架完成, 静态分析无法看到.
    本函数手动添加:
      Controller.method → TokenParseInterceptor.preHandle
    使 PathGenerator 能找到从 API 入口经 interceptor 到达 JwtUtils.parseToken sink 的路径.
    """
    INTERCEPTOR_NODE = "io.github.xxyopen.novel.config.interceptor.TokenParseInterceptor.preHandle"
    # 只有 /api/front/book/content/* 路径注册了 TokenParseInterceptor
    CONTENT_CONTROLLER = "io.github.xxyopen.novel.book.controller.front.FrontBookController.getBookContentAbout"

    added = 0
    if INTERCEPTOR_NODE not in cg.nodes or CONTENT_CONTROLLER not in cg.nodes:
        return added

    # 添加 Controller → Interceptor 边 (模拟 Spring 框架调度)
    if INTERCEPTOR_NODE not in [e.callee_id for e in cg.edges if e.caller_id == CONTENT_CONTROLLER]:
        edge = CallGraphEdge(caller_id=CONTENT_CONTROLLER, callee_id=INTERCEPTOR_NODE, is_taint=True)
        cg.add_edge(edge)
        added += 1

    if added:
        cg.build_adjacency()
    return added


def _add_virtual_dispatch_edges(cg: CallGraph) -> int:
    """
    添加接口→实现的虚分派边 (CHA: Class Hierarchy Analysis).

    Joern 源码分析只能看到对接口方法的调用 (如 BookService.getBookContentAbout),
    但实际运行时会分派到实现类 (如 BookServiceImpl.getBookContentAbout).

    本函数通过名称匹配添加 interface → impl 边, 使 PathGenerator 能找到完整路径.
    """
    # 找出所有可能的接口→实现对
    # 启发式: 节点 A.method 如果存在 AImpl.method, 则添加 A.method → AImpl.method 边
    added = 0
    node_ids = set(cg.nodes.keys())

    for node_id in list(node_ids):
        # 找 "接口" 节点: 有入边但无出边的方法
        if cg.successors(node_id):
            continue  # 已有后继, 不是叶子接口方法

        # 尝试找对应的 Impl 类
        # e.g. "io.x.service.BookService.getMethod" → "io.x.service.impl.BookServiceImpl.getMethod"
        parts = node_id.rsplit(".", 1)
        if len(parts) != 2:
            continue
        class_part, method_part = parts

        # 策略 1: ClassName → ClassNameImpl (同包或子包)
        impl_candidates = []
        for candidate_id in node_ids:
            if candidate_id == node_id:
                continue
            if not candidate_id.endswith(f".{method_part}"):
                continue
            cand_class = candidate_id.rsplit(".", 1)[0]
            # 检查是否是实现类 (含 Impl 或在 .impl. 子包中)
            if (cand_class.endswith(class_part.split(".")[-1] + "Impl")
                    or f".impl.{class_part.split('.')[-1]}Impl" in cand_class):
                impl_candidates.append(candidate_id)

        for impl_id in impl_candidates:
            edge = CallGraphEdge(caller_id=node_id, callee_id=impl_id, is_taint=False)
            cg.add_edge(edge)
            added += 1

    if added:
        cg.build_adjacency()
    return added

# ═══════════════════════════════════════════════════════════════════════════════
# Step 1: Joern 调用图
# ═══════════════════════════════════════════════════════════════════════════════

def step1_joern_call_graph(force: bool = False) -> CallGraph:
    """使用 Joern 生成 novel-cloud 调用图."""
    logger.info("=" * 60)
    logger.info("Step 1: Joern 生成调用图")
    logger.info("=" * 60)

    if os.path.exists(JOERN_CG_CACHE) and not force:
        logger.info(f"  使用缓存: {JOERN_CG_CACHE}")
        cg = load_call_graph_from_json(JOERN_CG_CACHE, PACKAGE_FILTER)
        logger.info(f"  调用图: {len(cg.nodes)} 节点, {len(cg.edges)} 边")
        # 添加接口→实现虚分派边
        added = _add_virtual_dispatch_edges(cg)
        logger.info(f"  虚分派边: +{added}")
        # 添加拦截器合成边
        added_interceptor = _add_interceptor_edges(cg)
        logger.info(f"  拦截器边: +{added_interceptor}")
        return cg

    # Joern 扫描 novel-book + novel-core 源码
    # 用 novel-cloud 根目录让 Joern 递归扫描所有子模块
    config = JoernConfig(
        joern_home=JOERN_HOME,
        package_filter=PACKAGE_FILTER,
        jvm_memory="4G",
    )
    adapter = JoernAdapter(config=config)

    if not adapter.is_available():
        logger.error("Joern 不可用! 请检查安装路径: " + JOERN_HOME)
        sys.exit(1)

    logger.info(f"  源码目录: {NOVEL_CLOUD_ROOT}")
    logger.info(f"  包过滤: {PACKAGE_FILTER}")

    cg = adapter.generate_call_graph(NOVEL_CLOUD_ROOT, PACKAGE_FILTER)

    # 添加接口→实现虚分派边
    added = _add_virtual_dispatch_edges(cg)
    logger.info(f"  虚分派边: +{added}")

    # 添加拦截器合成边
    added_interceptor = _add_interceptor_edges(cg)
    logger.info(f"  拦截器边: +{added_interceptor}")

    # 保存缓存 (序列化为 JSON)
    _save_call_graph_json(cg, JOERN_CG_CACHE)
    logger.info(f"  调用图: {len(cg.nodes)} 节点, {len(cg.edges)} 边")
    logger.info(f"  已保存: {JOERN_CG_CACHE}")
    return cg


# ═══════════════════════════════════════════════════════════════════════════════
# Step 2: CodeQL 污点分析
# ═══════════════════════════════════════════════════════════════════════════════

def step2_codeql_taint(cg: CallGraph, force: bool = False) -> CallGraph:
    """使用 CodeQL 进行污点分析, 标记调用图 taint 边."""
    logger.info("")
    logger.info("=" * 60)
    logger.info("Step 2: CodeQL 污点分析")
    logger.info("=" * 60)

    if os.path.exists(CODEQL_CSV_PATH) and not force:
        logger.info(f"  使用缓存: {CODEQL_CSV_PATH}")
        config = CodeQLConfig(codeql_home=CODEQL_HOME)
        adapter = CodeQLAdapter(config=config)
        result = adapter.mark_taint_edges_from_results(cg, CODEQL_CSV_PATH)
        logger.info(f"  Taint 对: {result.total_taint_pairs}, 标记边: {result.marked_edges}")
        return cg

    config = CodeQLConfig(
        codeql_home=CODEQL_HOME,
        package_filter=PACKAGE_FILTER,
        query_type="method_pairs",
    )
    adapter = CodeQLAdapter(config=config)

    if not adapter.is_available():
        logger.warning("CodeQL 不可用, 跳过污点分析")
        return cg

    logger.info(f"  项目根: {NOVEL_CLOUD_ROOT}")
    logger.info(f"  数据库: {CODEQL_DB_PATH}")

    try:
        result = adapter.mark_taint_edges(
            cg,
            source_root=NOVEL_CLOUD_ROOT,
            package_filter=PACKAGE_FILTER,
            database_dir=CODEQL_DB_PATH,
            build_command="mvn compile -DskipTests -q -pl novel-book/novel-book-service,novel-core -am",
        )
        logger.info(f"  Taint 对: {result.total_taint_pairs}, 标记边: {result.marked_edges}")

        # 保存结果 CSV (通过重新运行查询得到的)
        logger.info(f"  结果已缓存")
    except RuntimeError as e:
        logger.warning(f"  CodeQL 失败: {e}")
        logger.warning("  继续使用未标记的调用图")

    return cg


# ═══════════════════════════════════════════════════════════════════════════════
# Step 3: API 发现 + 预期路径生成
# ═══════════════════════════════════════════════════════════════════════════════

def step3_generate_paths(cg: CallGraph) -> list[tuple[APIEntry, Sink, ExpectedPath]]:
    """发现 API 入口, 加载 Sink, 生成预期路径."""
    logger.info("")
    logger.info("=" * 60)
    logger.info("Step 3: API 发现 + 预期路径生成")
    logger.info("=" * 60)

    # 3.1 自动发现 API 入口
    logger.info("  3.1 扫描 API 入口...")
    api_entries = discover_api_entries(NOVEL_CLOUD_ROOT)
    # 只保留 book-service 的 API (我们的目标容器)
    book_entries = [e for e in api_entries if "novel.book" in e.class_name or "novel_book" in e.class_name]
    logger.info(f"  发现 API 入口: {len(api_entries)} 总, {len(book_entries)} book-service")
    for e in book_entries:
        logger.info(f"    {e.http_method} {e.http_path} → {e.class_name.split('.')[-1]}.{e.method}")

    # 3.2 加载 Sink 信息
    logger.info("  3.2 加载日志 Sink...")
    sinks = _load_sinks_from_json(SINKS_JSON)
    # 只保留 book-service 能触发的 sink (排除 novel-search 等不在本容器的)
    reachable_sinks = [s for s in sinks if "search" not in s.class_name.lower()]
    logger.info(f"  Sink 总数: {len(sinks)}, 可达: {len(reachable_sinks)}")
    for s in reachable_sinks:
        logger.info(f"    [{s.sink_type.value}] {s.class_name.split('.')[-1]}.{s.method} L{s.line_number}")

    # 3.3 PathGenerator 生成预期路径
    logger.info("  3.3 生成预期路径...")
    # 将 Sink 转为 LogSink (PathGenerator 需要的格式)
    log_sinks = [
        LogSink(class_name=s.class_name, method=s.method, log_level=s.sink_type.value)
        for s in reachable_sinks
    ]

    generator = PathGenerator(max_path_length=10, max_paths_per_pair=1)
    path_set = generator.generate(
        project_name=PROJECT_NAME,
        api_entries=book_entries,
        log_sinks=log_sinks,
        call_graph=cg,
    )

    logger.info(f"  生成预期路径: {len(path_set.all_paths)} 条")

    # 3.4 组装 fuzz 目标: (API, Sink, Path)
    # 将 ExpectedPath 与对应的 Sink 对象关联
    targets: list[tuple[APIEntry, Sink, ExpectedPath]] = []
    sink_map = {f"{s.class_name}.{s.method}": s for s in reachable_sinks}

    for path in path_set.all_paths:
        # 找到对应的 Sink 对象
        sink_key = f"{path.log_sink.class_name}.{path.log_sink.method}"
        sink = sink_map.get(sink_key)
        if sink:
            targets.append((path.api_entry, sink, path))

    # 按 taint 优先 + 路径短 排序, 每个 API 只保留一条最佳路径
    targets = _select_best_targets(targets)

    logger.info(f"  Fuzz 目标 (去重后): {len(targets)} 条")
    for api, sink, path in targets:
        taint_str = "TAINT" if path.source == PathSource.TAINT else "CG"
        logger.info(f"    [{taint_str}] {api.http_method} {api.http_path} → {sink.class_name.split('.')[-1]}.{sink.method}")

    return targets


def _load_sinks_from_json(json_path: str) -> list[Sink]:
    """从 logging-sinks.json 加载 Sink 列表."""
    with open(json_path) as f:
        data = json.load(f)

    sinks: list[Sink] = []
    for r in data.get("results", []):
        # 映射 level → SinkType
        level = r.get("level", "INFO").upper()
        sink_type_map = {
            "ERROR": SinkType.LOG_ERROR,
            "WARN": SinkType.LOG_WARN,
            "INFO": SinkType.LOG_INFO,
            "DEBUG": SinkType.LOG_DEBUG,
        }
        sink_type = sink_type_map.get(level, SinkType.LOG_INFO)

        # 提取 tainted params (有 name 的参数)
        tainted = [p["name"] for p in r.get("params", []) if p.get("name")]

        sinks.append(Sink(
            class_name=r["className"],
            method=r["methodSignature"].split("(")[0],  # 取方法名部分
            line_number=r.get("lineNumber", -1),
            src_file=r.get("filePath", "").split("/")[-1],
            sink_type=sink_type,
            log_message=r.get("rawCallExpression", ""),
            tainted_params=tainted,
        ))

    return sinks


def _select_best_targets(
    targets: list[tuple[APIEntry, Sink, ExpectedPath]],
) -> list[tuple[APIEntry, Sink, ExpectedPath]]:
    """
    每个 API 入口只保留一条最佳路径:
      - taint 边多的优先
      - 路径短的优先
    """
    from collections import defaultdict

    by_api: dict[str, list[tuple[APIEntry, Sink, ExpectedPath]]] = defaultdict(list)
    for api, sink, path in targets:
        by_api[api.id].append((api, sink, path))

    best: list[tuple[APIEntry, Sink, ExpectedPath]] = []
    for api_id, group in by_api.items():
        # 排序: taint > CG, 短 > 长
        group.sort(key=lambda t: (
            0 if t[2].source == PathSource.TAINT else 1,
            t[2].path_length,
        ))
        best.append(group[0])

    return best


# ═══════════════════════════════════════════════════════════════════════════════
# Step 4: Fuzz 并检查 marker
# ═══════════════════════════════════════════════════════════════════════════════

def step4_fuzz(targets: list[tuple[APIEntry, Sink, ExpectedPath]]) -> None:
    """对每条预期路径运行 fuzz, 检查 marker."""
    logger.info("")
    logger.info("=" * 60)
    logger.info("Step 4: Fuzz + Marker 检查")
    logger.info("=" * 60)

    # LLM 配置
    llm = LLM()
    if not llm.api_key:
        logger.error("未设置 LLM_API_KEY! 请先: export LLM_API_KEY=sk-xxx")
        logger.error("或在 .env 文件中配置")
        sys.exit(1)

    logger.info(f"  LLM: {llm.model} @ {llm.base_url}")
    logger.info(f"  目标: {BASE_URL}")
    logger.info(f"  容器: {CONTAINER_NAME}")
    logger.info(f"  Marker: \"{ATTACK_MARKER}\"")
    logger.info(f"  路径数: {len(targets)}")

    # 构建 pipeline
    fuzzer = Fuzzer(
        llm=llm,
        base_url=BASE_URL,
        attack_marker=ATTACK_MARKER,
        source_root=NOVEL_CLOUD_ROOT,
        verbose=True,
    )

    def _check_log(marker: str, skip_lines: int) -> bool:
        return check_container_log_after_line(marker, skip_lines, CONTAINER_NAME)

    def _get_log_line_count() -> int:
        return get_container_log_line_count(CONTAINER_NAME)

    pipeline = Pipeline(
        fuzzer=fuzzer,
        execute_fn=execute_with_trace,
        check_log_fn=_check_log,
        get_log_line_count_fn=_get_log_line_count,
        max_attempts=10,
    )

    # 运行
    result = pipeline.run(targets)
    print("\n" + result.summary)


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="novel-cloud 全自动 Fuzz Pipeline")
    parser.add_argument("--force-joern", action="store_true", help="强制重新生成调用图")
    parser.add_argument("--force-codeql", action="store_true", help="强制重新运行 CodeQL")
    parser.add_argument("--skip-codeql", action="store_true", help="跳过 CodeQL (仅用 CG)")
    parser.add_argument("--skip-fuzz", action="store_true", help="只生成路径, 不 fuzz")
    parser.add_argument("--step", type=int, help="只运行指定步骤 (1-4)")
    args = parser.parse_args()

    # Step 1
    if not args.step or args.step == 1:
        cg = step1_joern_call_graph(force=args.force_joern)
    else:
        cg = load_call_graph_from_json(JOERN_CG_CACHE, PACKAGE_FILTER)

    # Step 2
    if not args.step or args.step == 2:
        if not args.skip_codeql:
            cg = step2_codeql_taint(cg, force=args.force_codeql)
        else:
            logger.info("\n跳过 CodeQL 污点分析")

    # Step 3
    if not args.step or args.step == 3:
        targets = step3_generate_paths(cg)
    else:
        targets = []

    # Step 4
    if not args.step or args.step == 4:
        if not args.skip_fuzz and targets:
            step4_fuzz(targets)
        elif not targets:
            logger.warning("无 fuzz 目标, 跳过 step 4")
        else:
            logger.info("\n跳过 Fuzz (--skip-fuzz)")

    logger.info("\n完成!")
