"""
codeql_adapter.py — CodeQL 污点分析集成适配层

将 CodeQL 的 taint tracking 结果用于标记 CallGraph 中的边.

工作流程:
  1. 使用 codeql database create 为 Java 项目创建 CodeQL 数据库
  2. 运行自定义 taint tracking query, 导出 source→sink 的数据流路径
  3. 将路径中的 (caller, callee) 边标记为 is_taint=True

依赖:
  - CodeQL CLI 已安装 (codeql 在 PATH 中)
  - CodeQL bundle (含 java-all/java-queries qlpacks)

参考 GONDAR 论文:
  - CodeQL 用于 sink detection (CWE-specific queries)
  - 本模块聚焦: 从 source (API 入口) 到 sink (log/敏感 API) 的 taint 路径
  - taint 路径中的边被标记, 使 PathGenerator 优先选择这些路径
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from path_generator import CallGraph, CallGraphEdge

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# CodeQL 查询模板
# ═══════════════════════════════════════════════════════════════════════

# 通用 taint tracking query: 从 HTTP 入口到日志调用的数据流路径
# 输出: 路径经过的每个方法调用 (caller, callee, line)
TAINT_QUERY_TEMPLATE = """/**
 * @name Taint flow from HTTP source to log sink
 * @description Finds data flow paths from HTTP request parameters to logging calls
 * @kind path-problem
 * @id aiops/taint-to-log
 */

import java
import semmle.code.java.dataflow.TaintTracking
import semmle.code.java.dataflow.FlowSources

module TaintToLogConfig implements DataFlow::ConfigSig {{
  predicate isSource(DataFlow::Node source) {{
    source instanceof RemoteFlowSource
  }}

  predicate isSink(DataFlow::Node sink) {{
    exists(MethodCall mc |
      mc.getMethod().getDeclaringType().hasQualifiedName("org.slf4j", "Logger") or
      mc.getMethod().getDeclaringType().hasQualifiedName("org.apache.logging.log4j", "Logger") or
      mc.getMethod().getDeclaringType().hasQualifiedName("java.util.logging", "Logger") or
      mc.getMethod().getName().regexpMatch("(info|warn|error|debug|trace|log)") |
      sink.asExpr() = mc.getAnArgument()
    )
  }}
}}

module TaintToLogFlow = TaintTracking::Global<TaintToLogConfig>;
import TaintToLogFlow::PathGraph

from TaintToLogFlow::PathNode source, TaintToLogFlow::PathNode sink
where TaintToLogFlow::flowPath(source, sink)
select sink.getNode(), source, sink, "Taint flow from $@ to $@",
  source.getNode(), "source",
  sink.getNode(), "sink"
"""

# 更简单的查询: 直接输出 taint 路径经过的方法调用对
TAINT_EDGES_QUERY_TEMPLATE = """/**
 * @name Taint flow edges between methods
 * @description Outputs (caller_class, caller_method, callee_class, callee_method)
 *              pairs along taint flow paths
 * @kind problem
 * @id aiops/taint-edges
 */

import java
import semmle.code.java.dataflow.TaintTracking
import semmle.code.java.dataflow.FlowSources

module TaintConfig implements DataFlow::ConfigSig {{
  predicate isSource(DataFlow::Node source) {{
    source instanceof RemoteFlowSource
  }}

  predicate isSink(DataFlow::Node sink) {{
    exists(MethodCall mc |
      (
        mc.getMethod().getDeclaringType().hasQualifiedName("org.slf4j", "Logger") or
        mc.getMethod().getDeclaringType().hasQualifiedName("org.apache.logging.log4j", "Logger") or
        mc.getMethod().getDeclaringType().hasQualifiedName("java.util.logging", "Logger") or
        mc.getMethod().getName().regexpMatch("(info|warn|error|debug|trace|log)")
      ) |
      sink.asExpr() = mc.getAnArgument()
    )
    or
    // 自定义 sink: 任何可能的安全敏感操作
    exists(MethodCall mc |
      mc.getMethod().getName().regexpMatch("(exec|execute|query|eval|send|write|redirect)") |
      sink.asExpr() = mc.getAnArgument()
    )
  }}
}}

module TaintFlow = TaintTracking::Global<TaintConfig>;

// 输出 taint 路径经过的方法调用
from MethodCall call, Method caller, Method callee
where
  TaintFlow::flow(_, _) and
  call.getEnclosingCallable() = caller and
  call.getMethod() = callee and
  exists(DataFlow::Node n |
    TaintFlow::flow(_, n) and
    n.asExpr().getEnclosingCallable() = caller
  ) and
  // 过滤: 只保留用户代码
  caller.getCompilationUnit().fromSource() and
  callee.getCompilationUnit().fromSource()
select call,
  caller.getDeclaringType().getQualifiedName() + "." + caller.getName() + " -> " +
  callee.getDeclaringType().getQualifiedName() + "." + callee.getName()
"""

# 最实用版本: 直接找有 taint 流经过的 (caller, callee) 对, 输出 CSV
TAINT_METHOD_PAIRS_QUERY = """/**
 * @name Taint-carrying method call pairs
 * @description Method pairs where tainted data flows from caller to callee
 * @kind problem
 * @id aiops/taint-method-pairs
 * @problem.severity recommendation
 */

import java
import semmle.code.java.dataflow.TaintTracking
import semmle.code.java.dataflow.FlowSources

module TaintConfig implements DataFlow::ConfigSig {
  predicate isSource(DataFlow::Node source) {
    source instanceof RemoteFlowSource
  }

  predicate isSink(DataFlow::Node sink) {
    // 广义 sink: 任何用户代码中的方法调用参数
    exists(MethodCall mc |
      mc.getMethod().getCompilationUnit().fromSource() |
      sink.asExpr() = mc.getAnArgument()
    )
  }
}

module TaintFlow = TaintTracking::Global<TaintConfig>;

from MethodCall call, Method caller, Method callee, DataFlow::Node src, DataFlow::Node snk
where
  TaintFlow::flow(src, snk) and
  snk.asExpr() = call.getAnArgument() and
  call.getEnclosingCallable() = caller and
  call.getMethod() = callee and
  caller.getCompilationUnit().fromSource() and
  callee.getCompilationUnit().fromSource()
select call,
  caller.getDeclaringType().getQualifiedName() + "." + caller.getName() + "|" +
  callee.getDeclaringType().getQualifiedName() + "." + callee.getName()
"""

# 简化版: 用户可以自定义 source/sink pattern
CUSTOM_TAINT_QUERY_TEMPLATE = """/**
 * @name Custom taint flow edges
 * @description Taint edges for package {package_filter}
 * @kind problem
 * @id aiops/custom-taint
 * @problem.severity recommendation
 */

import java
import semmle.code.java.dataflow.TaintTracking
import semmle.code.java.dataflow.FlowSources

module CustomTaintConfig implements DataFlow::ConfigSig {{
  predicate isSource(DataFlow::Node source) {{
    source instanceof RemoteFlowSource
    or
    // Controller 方法参数也作为 source
    exists(Parameter p |
      p.getCallable().getDeclaringType().getAnAncestor().hasQualifiedName("org.springframework.stereotype", "Controller") or
      p.getCallable().hasAnnotation("org.springframework.web.bind.annotation", "RequestMapping") or
      p.getCallable().hasAnnotation("org.springframework.web.bind.annotation", "GetMapping") or
      p.getCallable().hasAnnotation("org.springframework.web.bind.annotation", "PostMapping") |
      source.asParameter() = p
    )
  }}

  predicate isSink(DataFlow::Node sink) {{
    exists(MethodCall mc |
      mc.getMethod().getCompilationUnit().fromSource() and
      mc.getMethod().getDeclaringType().getQualifiedName().matches("{package_filter}%") |
      sink.asExpr() = mc.getAnArgument()
    )
  }}
}}

module CustomTaintFlow = TaintTracking::Global<CustomTaintConfig>;

from MethodCall call, Method caller, Method callee, DataFlow::Node src, DataFlow::Node snk
where
  CustomTaintFlow::flow(src, snk) and
  snk.asExpr() = call.getAnArgument() and
  call.getEnclosingCallable() = caller and
  call.getMethod() = callee and
  caller.getDeclaringType().getQualifiedName().matches("{package_filter}%") and
  callee.getDeclaringType().getQualifiedName().matches("{package_filter}%")
select call,
  caller.getDeclaringType().getQualifiedName() + "." + caller.getName() + "|" +
  callee.getDeclaringType().getQualifiedName() + "." + callee.getName()
"""


# ═══════════════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class CodeQLConfig:
    """CodeQL 集成配置."""
    # CodeQL CLI 路径 (None = 从 PATH 查找)
    codeql_home: Optional[str] = None

    # 数据库选项
    source_root: str = ""          # Java 项目根目录 (含 pom.xml 或 build.gradle)
    database_dir: str = ""         # CodeQL 数据库输出目录
    language: str = "java"         # 语言

    # 查询选项
    package_filter: str = ""       # 目标包名 (e.g. "com.example.microservice")
    query_type: str = "custom"     # "builtin" | "custom" | "method_pairs"

    # 构建命令 (用于创建 CodeQL 数据库)
    build_command: str = ""        # e.g. "mvn compile -DskipTests"
    # 如果为空, CodeQL 会尝试自动检测构建系统

    # 超时
    db_create_timeout: int = 900   # 数据库创建超时 (秒)
    query_timeout: int = 600       # 查询超时 (秒)

    # 线程
    threads: int = 0               # 0 = 自动检测

    @property
    def codeql_bin(self) -> str:
        """获取 codeql 可执行文件路径."""
        if self.codeql_home:
            return os.path.join(self.codeql_home, "codeql")
        return shutil.which("codeql") or "codeql"


# ═══════════════════════════════════════════════════════════════════════
# CodeQL 适配器
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class CodeQLAdapter:
    """
    CodeQL 污点分析适配器.

    核心功能: 在已有的 CallGraph 上标记 taint 边.
    流程: 创建 DB → 运行 taint query → 解析结果 → 标记 CallGraph edges
    """
    config: CodeQLConfig

    def is_available(self) -> bool:
        """检查 CodeQL 是否已安装且可用."""
        try:
            result = subprocess.run(
                [self.config.codeql_bin, "version"],
                capture_output=True, text=True, timeout=10
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def mark_taint_edges(self, call_graph: CallGraph,
                         source_root: str,
                         package_filter: str = "",
                         database_dir: str = "",
                         build_command: str = "") -> TaintResult:
        """
        对已有 CallGraph 的边标记 taint.

        流程:
          1. 创建 CodeQL 数据库 (如果不存在)
          2. 运行 taint tracking query
          3. 解析查询结果, 在 CallGraph 中标记对应边

        Args:
            call_graph: 要标记的调用图 (会被修改)
            source_root: Java 项目根目录
            package_filter: 包过滤器
            database_dir: CodeQL 数据库路径 (空 = 自动创建临时的)
            build_command: 构建命令

        Returns:
            TaintResult 包含标记统计
        """
        if package_filter:
            self.config.package_filter = package_filter
        if build_command:
            self.config.build_command = build_command

        # 确定数据库路径
        use_temp_db = not database_dir
        if use_temp_db:
            db_dir = tempfile.mkdtemp(prefix="codeql_db_")
        else:
            db_dir = database_dir

        try:
            # Step 1: 创建数据库 (如果目录不存在或为空)
            if not os.path.exists(os.path.join(db_dir, "db-java")):
                logger.info(f"Creating CodeQL database for: {source_root}")
                self._create_database(source_root, db_dir)
            else:
                logger.info(f"Using existing CodeQL database: {db_dir}")

            # Step 2: 运行 taint query
            logger.info("Running taint tracking query...")
            taint_pairs = self._run_taint_query(db_dir)

            # Step 3: 标记 CallGraph 边
            logger.info(f"Found {len(taint_pairs)} taint pairs, marking edges...")
            marked_count = self._mark_edges(call_graph, taint_pairs)

            return TaintResult(
                total_taint_pairs=len(taint_pairs),
                marked_edges=marked_count,
                database_path=db_dir,
            )
        finally:
            if use_temp_db:
                # 保留 DB 用于调试, 但记录路径
                logger.info(f"CodeQL database at: {db_dir} (temp, not deleted)")

    def mark_taint_edges_from_results(
        self, call_graph: CallGraph, results_path: str
    ) -> TaintResult:
        """
        从已有的 CodeQL 查询结果文件标记 taint 边.

        适用于: 查询已运行, 结果已缓存的场景.

        Args:
            call_graph: 要标记的调用图
            results_path: CodeQL 查询结果文件 (CSV/SARIF)

        Returns:
            TaintResult
        """
        taint_pairs = self._parse_results_file(results_path)
        marked_count = self._mark_edges(call_graph, taint_pairs)
        return TaintResult(
            total_taint_pairs=len(taint_pairs),
            marked_edges=marked_count,
            database_path="",
        )

    def _create_database(self, source_root: str, db_dir: str) -> None:
        """Step 1: 创建 CodeQL 数据库."""
        cmd = [
            self.config.codeql_bin,
            "database", "create",
            db_dir,
            f"--language={self.config.language}",
            f"--source-root={source_root}",
            "--overwrite",
        ]

        if self.config.build_command:
            cmd.extend(["--command", self.config.build_command])

        if self.config.threads:
            cmd.extend([f"--threads={self.config.threads}"])

        logger.debug(f"Running: {' '.join(cmd)}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self.config.db_create_timeout,
            cwd=source_root,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"codeql database create failed (exit {result.returncode}):\n"
                f"stderr: {result.stderr[:3000]}"
            )

        logger.info(f"CodeQL database created: {db_dir}")

    def _run_taint_query(self, db_dir: str) -> list[tuple[str, str]]:
        """
        Step 2: 运行 taint tracking query, 返回 (caller_id, callee_id) 对.
        """
        with tempfile.TemporaryDirectory(prefix="codeql_query_") as tmpdir:
            # 写入查询文件
            query_path = os.path.join(tmpdir, "taint_edges.ql")
            results_path = os.path.join(tmpdir, "results.csv")

            query_content = self._get_query_content()
            with open(query_path, 'w') as f:
                f.write(query_content)

            # 运行查询
            # 首先需要一个 qlpack.yml
            qlpack_path = os.path.join(tmpdir, "qlpack.yml")
            with open(qlpack_path, 'w') as f:
                f.write(
                    "name: aiops/taint-query\n"
                    "version: 0.0.1\n"
                    "dependencies:\n"
                    "  codeql/java-all: \"*\"\n"
                )

            # 安装依赖
            install_cmd = [
                self.config.codeql_bin, "pack", "install", tmpdir
            ]
            subprocess.run(
                install_cmd, capture_output=True, text=True,
                timeout=120
            )

            # 运行查询
            cmd = [
                self.config.codeql_bin,
                "query", "run",
                query_path,
                f"--database={db_dir}",
                f"--output={results_path}.bqrs",
            ]

            if self.config.threads:
                cmd.append(f"--threads={self.config.threads}")

            logger.debug(f"Running query: {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.config.query_timeout,
            )

            if result.returncode != 0:
                raise RuntimeError(
                    f"codeql query run failed (exit {result.returncode}):\n"
                    f"stderr: {result.stderr[:3000]}"
                )

            # 转换 BQRS → CSV
            decode_cmd = [
                self.config.codeql_bin,
                "bqrs", "decode",
                f"{results_path}.bqrs",
                "--format=csv",
                f"--output={results_path}",
            ]

            result = subprocess.run(
                decode_cmd,
                capture_output=True,
                text=True,
                timeout=60,
            )

            if result.returncode != 0:
                raise RuntimeError(
                    f"codeql bqrs decode failed:\n{result.stderr[:2000]}"
                )

            # 解析 CSV 结果
            return self._parse_csv_results(results_path)

    def _get_query_content(self) -> str:
        """根据配置选择查询模板."""
        if self.config.query_type == "method_pairs":
            return TAINT_METHOD_PAIRS_QUERY
        elif self.config.query_type == "custom" and self.config.package_filter:
            return CUSTOM_TAINT_QUERY_TEMPLATE.format(
                package_filter=self.config.package_filter
            )
        else:
            return TAINT_METHOD_PAIRS_QUERY

    def _parse_csv_results(self, csv_path: str) -> list[tuple[str, str]]:
        """
        解析 CodeQL CSV 输出, 提取 (caller_id, callee_id) 对.

        CSV 格式 (我们的 query 输出):
          col0 (call location), col1 ("CallerClass.callerMethod|CalleeClass.calleeMethod")
        """
        pairs: list[tuple[str, str]] = []

        if not os.path.exists(csv_path):
            logger.warning(f"No results file: {csv_path}")
            return pairs

        with open(csv_path, 'r') as f:
            import csv
            reader = csv.reader(f)
            # 跳过 header
            next(reader, None)
            for row in reader:
                if len(row) >= 2:
                    # 第二列是 "caller|callee" 格式
                    pair_str = row[1] if len(row) > 1 else row[0]
                    if "|" in pair_str:
                        caller, callee = pair_str.split("|", 1)
                        pairs.append((caller.strip(), callee.strip()))

        logger.info(f"Parsed {len(pairs)} taint pairs from CSV")
        return pairs

    def _parse_results_file(self, results_path: str) -> list[tuple[str, str]]:
        """解析已有的结果文件 (支持 CSV 和 SARIF)."""
        if results_path.endswith(".sarif") or results_path.endswith(".json"):
            return self._parse_sarif_results(results_path)
        else:
            return self._parse_csv_results(results_path)

    def _parse_sarif_results(self, sarif_path: str) -> list[tuple[str, str]]:
        """解析 SARIF 格式的 CodeQL 结果."""
        pairs: list[tuple[str, str]] = []

        with open(sarif_path, 'r') as f:
            data = json.load(f)

        for run in data.get("runs", []):
            for result in run.get("results", []):
                message = result.get("message", {}).get("text", "")
                if "|" in message:
                    # 从 message 中提取 caller|callee
                    for part in message.split():
                        if "|" in part:
                            caller, callee = part.split("|", 1)
                            pairs.append((caller.strip(), callee.strip()))

        return pairs

    def _mark_edges(self, call_graph: CallGraph,
                    taint_pairs: list[tuple[str, str]]) -> int:
        """
        Step 3: 在 CallGraph 中标记 taint 边.

        匹配策略:
          - 精确匹配: caller_id == edge.caller_id and callee_id == edge.callee_id
          - 模糊匹配: 只比较 className.methodName (忽略签名差异)
        """
        # 建立 taint pair 集合 (用于快速查找)
        taint_set: set[tuple[str, str]] = set(taint_pairs)

        # 也建立简化版 (只取最后的 Class.method 部分)
        taint_set_simple: set[tuple[str, str]] = set()
        for caller, callee in taint_pairs:
            # "com.example.Service.method" → 保持原样 (已经是 node_id 格式)
            taint_set_simple.add((caller, callee))
            # 也添加短名匹配
            caller_short = caller.rsplit(".", 1)[-1] if "." in caller else caller
            callee_short = callee.rsplit(".", 1)[-1] if "." in callee else callee
            # 不用短名, 太容易误匹配

        marked = 0
        for edge in call_graph.edges:
            # 精确匹配
            if (edge.caller_id, edge.callee_id) in taint_set:
                edge.is_taint = True
                marked += 1
                continue

            # 标准化后匹配 (去掉可能的签名部分)
            caller_norm = edge.caller_id.split(":")[0] if ":" in edge.caller_id else edge.caller_id
            callee_norm = edge.callee_id.split(":")[0] if ":" in edge.callee_id else edge.callee_id
            if (caller_norm, callee_norm) in taint_set:
                edge.is_taint = True
                marked += 1

        # 重建 adjacency (更新 _taint_edges 集合)
        call_graph.build_adjacency()

        logger.info(f"Marked {marked}/{len(call_graph.edges)} edges as taint")
        return marked


# ═══════════════════════════════════════════════════════════════════════
# 结果数据类
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class TaintResult:
    """CodeQL taint 分析结果."""
    total_taint_pairs: int = 0     # 查询发现的 taint 对总数
    marked_edges: int = 0          # 成功标记的 CallGraph 边数
    database_path: str = ""        # CodeQL 数据库路径 (可复用)

    @property
    def summary(self) -> str:
        return (
            f"Taint analysis: {self.total_taint_pairs} pairs found, "
            f"{self.marked_edges} edges marked"
        )


# ═══════════════════════════════════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════════════════════════════════

def mark_taint_on_call_graph(
    call_graph: CallGraph,
    source_root: str,
    package_filter: str = "",
    build_command: str = "mvn compile -DskipTests -q",
    codeql_home: Optional[str] = None,
    database_dir: str = "",
) -> TaintResult:
    """
    便捷函数: 对 CallGraph 运行 CodeQL taint 分析并标记边.

    典型使用:
        >>> cg = generate_call_graph_from_source(...)  # from joern_adapter
        >>> result = mark_taint_on_call_graph(
        ...     cg,
        ...     source_root="examples/java-microservice",
        ...     package_filter="com.example.microservice",
        ... )
        >>> print(result.summary)

    Args:
        call_graph: Joern 生成的调用图
        source_root: Java 项目根目录
        package_filter: 目标包名
        build_command: Maven/Gradle 构建命令
        codeql_home: CodeQL 安装路径
        database_dir: 数据库缓存路径 (空 = 临时)

    Returns:
        TaintResult
    """
    config = CodeQLConfig(
        codeql_home=codeql_home,
        source_root=source_root,
        package_filter=package_filter,
        build_command=build_command,
    )
    adapter = CodeQLAdapter(config=config)

    if not adapter.is_available():
        raise RuntimeError(
            "CodeQL not found. Install via:\n"
            "  Download from https://github.com/github/codeql-action/releases\n"
            "  Extract and add to PATH.\n"
            "Or specify codeql_home parameter."
        )

    return adapter.mark_taint_edges(
        call_graph, source_root, package_filter, database_dir, build_command
    )


def load_taint_from_csv(
    call_graph: CallGraph,
    csv_path: str,
) -> TaintResult:
    """
    便捷函数: 从已有 CSV 结果标记 taint 边.

    CSV 格式: 每行 "CallerClass.method|CalleeClass.method"
    """
    config = CodeQLConfig()
    adapter = CodeQLAdapter(config=config)
    return adapter.mark_taint_edges_from_results(call_graph, csv_path)
