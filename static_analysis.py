"""
static_analysis.py — 静态分析统一入口

将 Joern (调用图) + CodeQL (taint 标记) + PathGenerator (路径搜索) 集成为一个完整流水线.

使用方式:
    from static_analysis import analyze_project

    result = analyze_project(
        source_dir="examples/java-microservice/src/main/java",
        project_name="java-microservice",
        package_filter="com.example.microservice",
        api_entries=[...],  # 可选, 不提供则跳过路径生成
        log_sinks=[...],    # 可选
    )

    # result.call_graph — 带 taint 标记的调用图
    # result.path_set   — 预期路径集合 (如果提供了 api_entries + log_sinks)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from path_generator import CallGraph, PathGenerator
from expected_path import APIEntry, LogSink, ExpectedPathSet
from joern_adapter import (
    JoernAdapter, JoernConfig, JoernDockerAdapter,
    generate_call_graph_from_source, load_call_graph_from_json,
)
from codeql_adapter import (
    CodeQLAdapter, CodeQLConfig, TaintResult,
    mark_taint_on_call_graph, load_taint_from_csv,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# 分析结果
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class StaticAnalysisResult:
    """静态分析完整结果."""
    project_name: str
    call_graph: CallGraph
    taint_result: Optional[TaintResult] = None
    path_set: Optional[ExpectedPathSet] = None

    # 统计
    total_methods: int = 0
    total_edges: int = 0
    taint_edges: int = 0
    total_paths: int = 0

    @property
    def summary(self) -> str:
        parts = [
            f"Project: {self.project_name}",
            f"  Methods: {self.total_methods}",
            f"  Call edges: {self.total_edges}",
            f"  Taint edges: {self.taint_edges}",
        ]
        if self.path_set:
            parts.append(f"  Expected paths: {self.total_paths}")
        return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════
# 主流水线
# ═══════════════════════════════════════════════════════════════════════

def analyze_project(
    source_dir: str,
    project_name: str = "",
    package_filter: str = "",
    api_entries: Optional[list[APIEntry]] = None,
    log_sinks: Optional[list[LogSink]] = None,
    # Joern 选项
    joern_home: Optional[str] = None,
    use_docker_joern: bool = False,
    joern_cache_json: str = "",     # 已有的 Joern JSON (跳过 Joern 分析)
    # CodeQL 选项
    codeql_home: Optional[str] = None,
    skip_taint: bool = False,       # 跳过 CodeQL taint 分析
    codeql_database_dir: str = "",  # CodeQL 数据库缓存
    codeql_results_csv: str = "",   # 已有的 taint CSV (跳过 CodeQL 分析)
    build_command: str = "mvn compile -DskipTests -q",
    # PathGenerator 选项
    max_path_length: int = 20,
    jvm_memory: str = "4G",
) -> StaticAnalysisResult:
    """
    完整的静态分析流水线.

    阶段 1 (Joern): 生成调用图
    阶段 2 (CodeQL): 标记 taint 边
    阶段 3 (PathGenerator): 生成预期路径

    每个阶段都可以通过缓存文件跳过.

    Args:
        source_dir: Java 源码目录
        project_name: 项目名称
        package_filter: 包过滤器
        api_entries: API 入口列表 (None = 跳过路径生成)
        log_sinks: 日志 sink 列表 (None = 跳过路径生成)
        joern_home: Joern 安装路径
        use_docker_joern: 使用 Docker 运行 Joern
        joern_cache_json: 已有的调用图 JSON
        codeql_home: CodeQL 安装路径
        skip_taint: 是否跳过 taint 分析
        codeql_database_dir: CodeQL 数据库缓存路径
        codeql_results_csv: 已有的 taint 结果 CSV
        build_command: 构建命令
        max_path_length: 路径最大深度
        jvm_memory: JVM 内存

    Returns:
        StaticAnalysisResult
    """
    if not project_name:
        project_name = source_dir.rstrip("/").split("/")[-1]

    # ── 阶段 1: 调用图 ──────────────────────────────────────────────
    logger.info(f"=== Phase 1: Call Graph Generation ({project_name}) ===")

    if joern_cache_json:
        logger.info(f"Loading cached call graph from: {joern_cache_json}")
        call_graph = load_call_graph_from_json(joern_cache_json, package_filter)
    elif use_docker_joern:
        logger.info("Generating call graph via Joern Docker...")
        config = JoernConfig(
            package_filter=package_filter,
            jvm_memory=jvm_memory,
        )
        adapter = JoernDockerAdapter(config=config)
        call_graph = adapter.generate_call_graph(source_dir, package_filter)
    else:
        logger.info("Generating call graph via Joern CLI...")
        call_graph = generate_call_graph_from_source(
            source_dir, package_filter, joern_home, jvm_memory
        )

    logger.info(
        f"Call graph: {len(call_graph.nodes)} methods, {len(call_graph.edges)} edges"
    )

    # ── 阶段 2: Taint 标记 ───────────────────────────────────────────
    taint_result: Optional[TaintResult] = None

    if not skip_taint:
        logger.info(f"=== Phase 2: Taint Analysis ({project_name}) ===")

        if codeql_results_csv:
            logger.info(f"Loading cached taint results from: {codeql_results_csv}")
            taint_result = load_taint_from_csv(call_graph, codeql_results_csv)
        else:
            try:
                taint_result = mark_taint_on_call_graph(
                    call_graph,
                    source_root=source_dir,
                    package_filter=package_filter,
                    build_command=build_command,
                    codeql_home=codeql_home,
                    database_dir=codeql_database_dir,
                )
            except RuntimeError as e:
                logger.warning(f"CodeQL taint analysis failed: {e}")
                logger.warning("Continuing without taint marking.")
                taint_result = TaintResult()

        logger.info(taint_result.summary if taint_result else "Taint: skipped")
    else:
        logger.info("=== Phase 2: Taint Analysis SKIPPED ===")

    # ── 阶段 3: 路径生成 ─────────────────────────────────────────────
    path_set: Optional[ExpectedPathSet] = None

    if api_entries and log_sinks:
        logger.info(f"=== Phase 3: Path Generation ({project_name}) ===")
        logger.info(
            f"  API entries: {len(api_entries)}, Log sinks: {len(log_sinks)}"
        )

        generator = PathGenerator(max_path_length=max_path_length)
        path_set = generator.generate(
            project_name=project_name,
            api_entries=api_entries,
            log_sinks=log_sinks,
            call_graph=call_graph,
        )

        logger.info(f"Generated {len(path_set.all_paths)} expected paths")
    else:
        logger.info("=== Phase 3: Path Generation SKIPPED (no entries/sinks) ===")

    # ── 构建结果 ─────────────────────────────────────────────────────
    result = StaticAnalysisResult(
        project_name=project_name,
        call_graph=call_graph,
        taint_result=taint_result,
        path_set=path_set,
        total_methods=len(call_graph.nodes),
        total_edges=len(call_graph.edges),
        taint_edges=taint_result.marked_edges if taint_result else 0,
        total_paths=len(path_set.all_paths) if path_set else 0,
    )

    logger.info(f"\n{result.summary}")
    return result


# ═══════════════════════════════════════════════════════════════════════
# 仅调用图 (不需要 CodeQL)
# ═══════════════════════════════════════════════════════════════════════

def generate_call_graph_only(
    source_dir: str,
    package_filter: str = "",
    joern_home: Optional[str] = None,
    use_docker: bool = False,
    jvm_memory: str = "4G",
) -> CallGraph:
    """
    仅生成调用图, 不做 taint 分析.

    适用于只需要调用图结构的场景.
    """
    if use_docker:
        config = JoernConfig(package_filter=package_filter, jvm_memory=jvm_memory)
        adapter = JoernDockerAdapter(config=config)
        return adapter.generate_call_graph(source_dir, package_filter)
    else:
        return generate_call_graph_from_source(
            source_dir, package_filter, joern_home, jvm_memory
        )


# ═══════════════════════════════════════════════════════════════════════
# 工具可用性检查
# ═══════════════════════════════════════════════════════════════════════

def check_tools() -> dict[str, bool]:
    """
    检查所需工具是否已安装.

    Returns:
        {"joern": True/False, "codeql": True/False, "docker": True/False}
    """
    joern_adapter = JoernAdapter(config=JoernConfig())
    codeql_adapter = CodeQLAdapter(config=CodeQLConfig())
    docker_adapter = JoernDockerAdapter(config=JoernConfig())

    return {
        "joern": joern_adapter.is_available(),
        "codeql": codeql_adapter.is_available(),
        "docker": docker_adapter.is_available(),
    }
