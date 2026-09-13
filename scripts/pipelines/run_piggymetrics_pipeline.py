#!/usr/bin/env python3
"""
run_piggymetrics_pipeline.py — PiggyMetrics account-service 全自动化 Fuzz Pipeline

自动化步骤:
  1. Joern 生成调用图
  2. CodeQL 污点分析
  3. API 发现 + 预期路径生成 (利用 Sink JSON + taint CG)
  4. Fuzz 每条预期路径, 检查 marker

用法:
  set -a && source .env && set +a && .venv/bin/python run_piggymetrics_pipeline.py
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

PIGGY_ROOT = str(PROJECT_ROOT / "examples" / "PiggyMetrics")
# account-service 是目标服务的源码 (sink + 入口都在此)
PIGGY_SOURCES = [
    str(PROJECT_ROOT / "examples" / "PiggyMetrics" / "account-service"),
]

PACKAGE_FILTER = "com.piggymetrics"
PROJECT_NAME = "piggymetrics"

# 工具路径
JOERN_HOME = os.path.expanduser("~/bin/joern/joern-cli")
CODEQL_HOME = os.path.expanduser("~/bin/codeql")

# 缓存路径
JOERN_CG_CACHE = str(PROJECT_ROOT / "piggymetrics-callgraph.json")
CODEQL_DB_PATH = "/tmp/codeql-db-piggymetrics"
CODEQL_CSV_PATH = "/tmp/piggymetrics-taint-results.csv"

# Fuzz 配置
BASE_URL = "http://localhost:8080"
CONTAINER_NAME = "trace-real-piggymetrics-account-service"
ATTACK_MARKER = "FUZZ_MARKER_7x9k"
SINKS_JSON = str(PROJECT_ROOT / "sinks" / "piggymetrics-logging-sinks.json")

# Sink 白名单: 只 fuzz account-service 容器内可达的 sink
# (notification/statistics/auth 的 sink 不在本容器, 不可达)
# 格式: "{className}.{methodName}"
# Sink 白名单: account-service 容器内可达的 sink
# 可达性标注 (2026-08-16 实测):
#   - ErrorHandler.processValidationError: ✅ 可达 (需预置同名用户触发 already exists)
#   - AccountServiceImpl.create: ❌ 结构性不可达 (authClient L51 先于 log L67 失败, auth-service 未部署)
#   - AccountServiceImpl.saveChanges: ❌ 需 OAuth2 (PUT /current 返回 401)
#   - CustomUserInfoTokenServices.*: ❌ OAuth2 认证链路, 需有效 token, 无法从 API 入口到达
# 格式: "{className}.{methodName}"
SINK_WHITELIST = {
    "com.piggymetrics.account.controller.ErrorHandler.processValidationError",
    "com.piggymetrics.account.service.AccountServiceImpl.create",
    "com.piggymetrics.account.service.AccountServiceImpl.saveChanges",
    "com.piggymetrics.account.client.StatisticsServiceClientFallback.updateStatistics",
    "com.piggymetrics.account.service.security.CustomUserInfoTokenServices.loadAuthentication",
    "com.piggymetrics.account.service.security.CustomUserInfoTokenServices.getMap",
}


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
            "caller": edge.caller_id,
            "callee": edge.callee_id,
            "line": edge.call_line,
        })

    data = {"methods": methods, "calls": calls}
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _add_exception_handler_edges(cg: CallGraph) -> int:
    """
    添加 Spring @ExceptionHandler 合成边.

    ErrorHandler.processValidationError 由 Spring MVC 框架在 Controller 抛出
    校验异常时反射调用, Joern 静态分析看不到该调用边. 手动添加:
      AccountController.createNewAccount → ErrorHandler.processValidationError
    使 PathGenerator 能找到从 API 入口到达 ErrorHandler sink 的路径.
    """
    CONTROLLER = "com.piggymetrics.account.controller.AccountController.createNewAccount"
    HANDLER = "com.piggymetrics.account.controller.ErrorHandler.processValidationError"

    added = 0

    def _has_edge(caller: str, callee: str) -> bool:
        return any(e.caller_id == caller and e.callee_id == callee for e in cg.edges)

    if HANDLER not in cg.nodes:
        logger.warning(f"  合成边: 未找到 handler 节点 {HANDLER}")
        return added
    if CONTROLLER not in cg.nodes:
        logger.warning(f"  合成边: 未找到入口节点 {CONTROLLER}")
        return added

    if not _has_edge(CONTROLLER, HANDLER):
        cg.add_edge(CallGraphEdge(caller_id=CONTROLLER, callee_id=HANDLER, is_taint=True))
        added += 1

    if added:
        cg.build_adjacency()
    return added


def _add_virtual_dispatch_edges(cg: CallGraph) -> int:
    """
    添加接口→实现的虚分派边 (CHA: Class Hierarchy Analysis).

    Joern 源码分析只能看到对接口方法的调用, 但实际运行时会分派到实现类.
    通过名称匹配添加 interface → impl 边, 使 PathGenerator 能找到完整路径.
    """
    added = 0
    node_ids = set(cg.nodes.keys())

    for node_id in list(node_ids):
        if cg.successors(node_id):
            continue

        parts = node_id.rsplit(".", 1)
        if len(parts) != 2:
            continue
        class_part, method_part = parts

        impl_candidates = []
        for candidate_id in node_ids:
            if candidate_id == node_id:
                continue
            if not candidate_id.endswith(f".{method_part}"):
                continue
            cand_class = candidate_id.rsplit(".", 1)[0]
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
    """使用 Joern 生成 PiggyMetrics account-service 调用图."""
    logger.info("=" * 60)
    logger.info("Step 1: Joern 生成调用图")
    logger.info("=" * 60)

    if os.path.exists(JOERN_CG_CACHE) and not force:
        logger.info(f"  使用缓存: {JOERN_CG_CACHE}")
        cg = load_call_graph_from_json(JOERN_CG_CACHE, PACKAGE_FILTER)
        logger.info(f"  调用图: {len(cg.nodes)} 节点, {len(cg.edges)} 边")
        added_vd = _add_virtual_dispatch_edges(cg)
        logger.info(f"  虚分派边: +{added_vd}")
        added_eh = _add_exception_handler_edges(cg)
        logger.info(f"  异常处理边: +{added_eh}")
        return cg

    config = JoernConfig(
        joern_home=JOERN_HOME,
        package_filter=PACKAGE_FILTER,
        jvm_memory="4G",
    )
    adapter = JoernAdapter(config=config)

    if not adapter.is_available():
        logger.error("Joern 不可用! 请检查安装路径: " + JOERN_HOME)
        sys.exit(1)

    # 只扫 account-service 模块 (sink + 入口都在此)
    scan_root = PIGGY_SOURCES[0]
    logger.info(f"  源码目录: {scan_root}")
    logger.info(f"  包过滤: {PACKAGE_FILTER}")

    cg = adapter.generate_call_graph(scan_root, PACKAGE_FILTER)

    added_vd = _add_virtual_dispatch_edges(cg)
    logger.info(f"  虚分派边: +{added_vd}")
    added_eh = _add_exception_handler_edges(cg)
    logger.info(f"  异常处理边: +{added_eh}")

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

    logger.info(f"  项目根: {PIGGY_ROOT}")
    logger.info(f"  数据库: {CODEQL_DB_PATH}")

    try:
        result = adapter.mark_taint_edges(
            cg,
            source_root=PIGGY_ROOT,
            package_filter=PACKAGE_FILTER,
            database_dir=CODEQL_DB_PATH,
            build_command="mvn compile -DskipTests -q -pl account-service -am",
        )
        logger.info(f"  Taint 对: {result.total_taint_pairs}, 标记边: {result.marked_edges}")
        logger.info(f"  结果已缓存")
    except RuntimeError as e:
        logger.warning(f"  CodeQL 失败: {e}")
        logger.warning("  继续使用未标记的调用图")

    return cg


# ═══════════════════════════════════════════════════════════════════════════════
# Step 3: API 发现 + 预期路径生成
# ═══════════════════════════════════════════════════════════════════════════════

def step3_generate_paths(cg: CallGraph, all_sinks: bool = False) -> list[tuple[APIEntry, Sink, ExpectedPath]]:
    """发现 API 入口, 加载 Sink, 生成预期路径."""
    logger.info("")
    logger.info("=" * 60)
    logger.info("Step 3: API 发现 + 预期路径生成")
    logger.info("=" * 60)

    logger.info("  3.1 扫描 API 入口...")
    api_entries = discover_api_entries(PIGGY_SOURCES[0])
    # account-service 单模块, 保留全部入口
    auth_entries = api_entries
    logger.info(f"  发现 API 入口: {len(api_entries)} 总")
    for e in auth_entries:
        logger.info(f"    {e.http_method} {e.http_path} → {e.class_name.split('.')[-1]}.{e.method}")

    logger.info("  3.2 加载日志 Sink...")
    sinks = _load_sinks_from_json(SINKS_JSON)
    if all_sinks:
        # 只保留 account-service 容器内的 sink (notification/statistics/auth 不在本容器)
        reachable_sinks = [s for s in sinks if ".account." in s.class_name]
        logger.info(f"  Sink 总数: {len(sinks)}, 可达 (account-service): {len(reachable_sinks)}")
    else:
        reachable_sinks = [
            s for s in sinks if f"{s.class_name}.{s.method}" in SINK_WHITELIST
        ]
        logger.info(f"  Sink 总数: {len(sinks)}, 白名单命中: {len(reachable_sinks)}")
    for s in reachable_sinks:
        logger.info(f"    [{s.sink_type.value}] {s.class_name.split('.')[-1]}.{s.method} L{s.line_number}")

    logger.info("  3.3 生成预期路径...")
    log_sinks = [
        LogSink(class_name=s.class_name, method=s.method, log_level=s.sink_type.value)
        for s in reachable_sinks
    ]

    generator = PathGenerator(max_path_length=10, max_paths_per_pair=1)
    path_set = generator.generate(
        project_name=PROJECT_NAME,
        api_entries=auth_entries,
        log_sinks=log_sinks,
        call_graph=cg,
    )

    logger.info(f"  生成预期路径: {len(path_set.all_paths)} 条")

    targets: list[tuple[APIEntry, Sink, ExpectedPath]] = []
    sink_map = {f"{s.class_name}.{s.method}": s for s in reachable_sinks}

    for path in path_set.all_paths:
        sink_key = f"{path.log_sink.class_name}.{path.log_sink.method}"
        sink = sink_map.get(sink_key)
        if sink:
            targets.append((path.api_entry, sink, path))

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
        level = r.get("level", "INFO").upper()
        sink_type_map = {
            "ERROR": SinkType.LOG_ERROR,
            "WARN": SinkType.LOG_WARN,
            "INFO": SinkType.LOG_INFO,
            "DEBUG": SinkType.LOG_DEBUG,
        }
        sink_type = sink_type_map.get(level, SinkType.LOG_INFO)

        tainted = [p["name"] for p in r.get("params", []) if p.get("name")]

        sinks.append(Sink(
            class_name=r["className"],
            method=r["methodSignature"].split("(")[0],
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
    """每个 (API, sink) 只保留一条最佳路径: taint 优先, 短路径优先."""
    from collections import defaultdict

    by_pair: dict[tuple[str, str], list[tuple[APIEntry, Sink, ExpectedPath]]] = defaultdict(list)
    for api, sink, path in targets:
        pair_key = (api.id, f"{sink.class_name}.{sink.method}")
        by_pair[pair_key].append((api, sink, path))

    best: list[tuple[APIEntry, Sink, ExpectedPath]] = []
    for pair_key, group in by_pair.items():
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

    llm = LLM()
    if not llm.api_key:
        logger.error("未设置 LLM_API_KEY! 请先: export LLM_API_KEY=sk-xxx 或在 .env 配置")
        sys.exit(1)

    logger.info(f"  LLM: {llm.model} @ {llm.base_url}")
    logger.info(f"  目标: {BASE_URL}")
    logger.info(f"  容器: {CONTAINER_NAME}")
    logger.info(f"  Marker: \"{ATTACK_MARKER}\"")
    logger.info(f"  路径数: {len(targets)}")

    fuzzer = Fuzzer(
        llm=llm,
        base_url=BASE_URL,
        attack_marker=ATTACK_MARKER,
        source_root=PIGGY_ROOT,
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

    result = pipeline.run(targets)
    print("\n" + result.summary)


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="PiggyMetrics account-service 全自动 Fuzz Pipeline")
    parser.add_argument("--force-joern", action="store_true", help="强制重新生成调用图")
    parser.add_argument("--force-codeql", action="store_true", help="强制重新运行 CodeQL")
    parser.add_argument("--skip-codeql", action="store_true", help="跳过 CodeQL (仅用 CG)")
    parser.add_argument("--skip-fuzz", action="store_true", help="只生成路径, 不 fuzz")
    parser.add_argument("--all-sinks", action="store_true", help="跑全部可达 sink")
    parser.add_argument("--step", type=int, help="只运行指定步骤 (1-4)")
    args = parser.parse_args()

    if not args.step or args.step == 1:
        cg = step1_joern_call_graph(force=args.force_joern)
    else:
        cg = load_call_graph_from_json(JOERN_CG_CACHE, PACKAGE_FILTER)

    if not args.step or args.step == 2:
        if not args.skip_codeql:
            cg = step2_codeql_taint(cg, force=args.force_codeql)
        else:
            logger.info("\n跳过 CodeQL 污点分析")

    if not args.step or args.step == 3:
        targets = step3_generate_paths(cg, all_sinks=args.all_sinks)
    else:
        targets = []

    if not args.step or args.step == 4:
        if not args.skip_fuzz and targets:
            step4_fuzz(targets)
        elif not targets:
            logger.warning("无 fuzz 目标, 跳过 step 4")
        else:
            logger.info("\n跳过 Fuzz (--skip-fuzz)")

    logger.info("\n完成!")
