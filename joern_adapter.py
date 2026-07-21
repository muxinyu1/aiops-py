"""
joern_adapter.py — Joern 调用图集成适配层

将 Joern 静态分析结果转换为 path_generator.py 中的 CallGraph 数据结构.

工作流程:
  1. 调用 joern-parse (或 javasrc2cpg) 将 Java 源码解析为 CPG
  2. 通过 joern --script 执行 CPGQL 查询, 导出调用图 (JSON 格式)
  3. 解析 JSON, 构建 CallGraph 对象

依赖:
  - Joern CLI 已安装 (joern, joern-parse 在 PATH 中)
  - 或指定 joern_home 路径

支持的调用图构建算法 (通过 Joern 配置):
  - CHA (Class Hierarchy Analysis): 所有可能子类实现
  - RTA (Rapid Type Analysis): 仅实际实例化的类
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

from path_generator import CallGraph, CallGraphNode, CallGraphEdge

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Joern 脚本模板
# ═══════════════════════════════════════════════════════════════════════

# 导出调用图的 CPGQL 脚本 — 输出 JSON 格式的 (caller, callee) 对
EXPORT_CALL_GRAPH_SCRIPT = r"""
@main def exec(cpgFile: String, outFile: String) = {
  importCpg(cpgFile)

  // 获取所有方法节点信息
  val methods = cpg.method.internal.map { m =>
    Map(
      "fullName" -> m.fullName,
      "name" -> m.name,
      "signature" -> m.signature,
      "filename" -> m.filename,
      "lineNumber" -> m.lineNumber.getOrElse(-1).toString,
      "className" -> m.fullName.split("\\.").dropRight(1).mkString(".")
    )
  }.l

  // 获取所有调用边 (caller -> callee)
  val calls = cpg.call.map { c =>
    Map(
      "callerFullName" -> c.method.fullName,
      "calleeFullName" -> c.methodFullName,
      "callerName" -> c.method.name,
      "calleeName" -> c.name,
      "lineNumber" -> c.lineNumber.getOrElse(-1).toString
    )
  }.l

  val result = Map(
    "methods" -> methods,
    "calls" -> calls
  )

  import scala.util.Using
  import java.io.PrintWriter
  Using(new PrintWriter(outFile)) { pw =>
    // 简单 JSON 序列化
    pw.println(ujson.write(result))
  }
}
"""

# 导出调用图 (更简洁版本, 直接用 toJson)
EXPORT_CALL_GRAPH_SCRIPT_V2 = r"""
@main def exec(cpgFile: String, outFile: String) = {
  importCpg(cpgFile)

  // 方法信息
  val methods = cpg.method.internal.map { m =>
    ujson.Obj(
      "fullName" -> m.fullName,
      "name" -> m.name,
      "signature" -> m.signature,
      "filename" -> m.filename,
      "lineNumber" -> m.lineNumber.getOrElse(-1),
      "className" -> m.fullName.split("\\.").dropRight(1).mkString(".")
    )
  }.l

  // 调用边
  val calls = cpg.call.filter(c => cpg.method.internal.fullName.toSet.contains(c.methodFullName)).map { c =>
    ujson.Obj(
      "callerFullName" -> c.method.fullName,
      "calleeFullName" -> c.methodFullName,
      "lineNumber" -> c.lineNumber.getOrElse(-1)
    )
  }.l

  val result = ujson.Obj("methods" -> methods, "calls" -> calls)
  os.write.over(os.Path(outFile), ujson.write(result))
}
"""

# 最简版本: 用 Joern 内置 JSON 输出
EXPORT_CALL_GRAPH_SCRIPT_SIMPLE = (
    '@main def exec(cpgFile: String, outFile: String) = {\n'
    '  importCpg(cpgFile)\n'
    '\n'
    '  val sb = new StringBuilder()\n'
    '  sb.append("{\\"methods\\":[")\n'
    '\n'
    '  var first = true\n'
    '  cpg.method.internal.foreach { m =>\n'
    '    if (!first) sb.append(",")\n'
    '    first = false\n'
    '    val fn = m.fullName.replace("\\"", "\\\\\\"") \n'
    '    val name = m.name.replace("\\"", "\\\\\\"") \n'
    '    val sig = m.signature.replace("\\"", "\\\\\\"") \n'
    '    val file = m.filename.replace("\\"", "\\\\\\"") \n'
    '    val line = m.lineNumber.getOrElse(-1)\n'
    '    val cls = fn.split("\\\\.").dropRight(1).mkString(".")\n'
    '    sb.append(s"""\n'
    '{"fullName":"${fn}","name":"${name}","signature":"${sig}","filename":"${file}","lineNumber":${line},"className":"${cls}"}\n'
    '""".trim)\n'
    '  }\n'
    '\n'
    '  sb.append("],\\"calls\\":[")\n'
    '  first = true\n'
    '  cpg.call.foreach { c =>\n'
    '    val calleeFn = c.methodFullName\n'
    '    if (cpg.method.internal.fullNameExact(calleeFn).nonEmpty) {\n'
    '      if (!first) sb.append(",")\n'
    '      first = false\n'
    '      val callerFn = c.method.fullName.replace("\\"", "\\\\\\"") \n'
    '      val calleeFnEsc = calleeFn.replace("\\"", "\\\\\\"") \n'
    '      val line = c.lineNumber.getOrElse(-1)\n'
    '      sb.append(s"""\n'
    '{"caller":"${callerFn}","callee":"${calleeFnEsc}","line":${line}}\n'
    '""".trim)\n'
    '    }\n'
    '  }\n'
    '\n'
    '  sb.append("]}")\n'
    '  val pw = new java.io.PrintWriter(outFile)\n'
    '  pw.print(sb.toString())\n'
    '  pw.close()\n'
    '}\n'
)


# ═══════════════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class JoernConfig:
    """Joern 集成配置."""
    # Joern 安装路径 (None = 从 PATH 查找)
    joern_home: Optional[str] = None

    # CPG 生成选项
    input_path: str = ""           # 要分析的 Java 源码目录
    output_dir: str = ""           # 输出目录 (CPG + JSON)
    language: str = "javasrc"      # 前端: javasrc (源码) 或 java (字节码)

    # 调用图选项
    # Joern 默认启用 CHA; 设置为 True 启用更精确的 RTA
    enable_rta: bool = True

    # 过滤选项
    package_filter: str = ""       # 只保留此包下的方法, e.g. "com.example"
    exclude_test: bool = True      # 排除 test 目录
    exclude_generated: bool = True  # 排除生成代码

    # 超时
    parse_timeout: int = 600       # CPG 生成超时 (秒)
    query_timeout: int = 300       # 查询超时 (秒)

    # JVM 内存
    jvm_memory: str = "4G"         # Joern JVM 最大内存

    @property
    def joern_bin(self) -> str:
        """获取 joern 可执行文件路径."""
        if self.joern_home:
            return os.path.join(self.joern_home, "joern")
        return shutil.which("joern") or "joern"

    @property
    def joern_parse_bin(self) -> str:
        """获取 joern-parse 可执行文件路径."""
        if self.joern_home:
            return os.path.join(self.joern_home, "joern-parse")
        return shutil.which("joern-parse") or "joern-parse"


# ═══════════════════════════════════════════════════════════════════════
# Joern 适配器
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class JoernAdapter:
    """
    Joern 调用图适配器.

    使用 Joern CLI 从 Java 源码/字节码生成调用图,
    转换为 path_generator.py 中的 CallGraph 对象.
    """
    config: JoernConfig

    def is_available(self) -> bool:
        """检查 Joern 是否已安装且可用."""
        try:
            # joern-parse --help 会返回帮助信息并退出
            result = subprocess.run(
                [self.config.joern_parse_bin, "--help"],
                capture_output=True, text=True, timeout=15
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def generate_call_graph(self, source_dir: str,
                            package_filter: str = "") -> CallGraph:
        """
        从 Java 源码目录生成调用图.

        Args:
            source_dir: Java 源码根目录
            package_filter: 包过滤器, 如 "com.example.microservice"

        Returns:
            CallGraph 对象
        """
        if package_filter:
            self.config.package_filter = package_filter

        with tempfile.TemporaryDirectory(prefix="joern_") as tmpdir:
            cpg_path = os.path.join(tmpdir, "cpg.bin")
            json_path = os.path.join(tmpdir, "callgraph.json")
            script_path = os.path.join(tmpdir, "export_cg.sc")

            # Step 1: 生成 CPG
            logger.info(f"Generating CPG for: {source_dir}")
            self._generate_cpg(source_dir, cpg_path)

            # Step 2: 导出调用图为 JSON
            logger.info("Exporting call graph from CPG...")
            self._export_call_graph(cpg_path, json_path, script_path)

            # Step 3: 解析 JSON → CallGraph
            logger.info("Parsing call graph JSON...")
            return self._parse_call_graph_json(json_path)

    def generate_call_graph_from_cpg(self, cpg_path: str,
                                     package_filter: str = "") -> CallGraph:
        """
        从已有的 CPG 文件生成调用图 (跳过 Step 1).

        Args:
            cpg_path: CPG 文件路径
            package_filter: 包过滤器

        Returns:
            CallGraph 对象
        """
        if package_filter:
            self.config.package_filter = package_filter

        with tempfile.TemporaryDirectory(prefix="joern_") as tmpdir:
            json_path = os.path.join(tmpdir, "callgraph.json")
            script_path = os.path.join(tmpdir, "export_cg.sc")

            self._export_call_graph(cpg_path, json_path, script_path)
            return self._parse_call_graph_json(json_path)

    def _generate_cpg(self, source_dir: str, cpg_path: str) -> None:
        """Step 1: 使用 joern-parse 生成 CPG."""
        cmd = [
            self.config.joern_parse_bin,
            source_dir,
            "--output", cpg_path,
            f"-J-Xmx{self.config.jvm_memory}",
        ]

        # 指定语言前端
        if self.config.language:
            cmd.extend(["--language", self.config.language])

        logger.debug(f"Running: {' '.join(cmd)}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self.config.parse_timeout,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"joern-parse failed (exit {result.returncode}):\n"
                f"stdout: {result.stdout[:2000]}\n"
                f"stderr: {result.stderr[:2000]}"
            )

        if not os.path.exists(cpg_path):
            raise FileNotFoundError(f"CPG not generated: {cpg_path}")

        logger.info(f"CPG generated: {cpg_path} ({os.path.getsize(cpg_path)} bytes)")

    def _export_call_graph(self, cpg_path: str, json_path: str,
                           script_path: str) -> None:
        """Step 2: 执行 CPGQL 脚本导出调用图."""
        # 写入查询脚本
        with open(script_path, 'w') as f:
            f.write(EXPORT_CALL_GRAPH_SCRIPT_SIMPLE)

        cmd = [
            self.config.joern_bin,
            "--script", script_path,
            "--param", f"cpgFile={cpg_path}",
            "--param", f"outFile={json_path}",
            f"-J-Xmx{self.config.jvm_memory}",
        ]

        logger.debug(f"Running: {' '.join(cmd)}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self.config.query_timeout,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"joern script failed (exit {result.returncode}):\n"
                f"stdout: {result.stdout[:2000]}\n"
                f"stderr: {result.stderr[:2000]}"
            )

        if not os.path.exists(json_path):
            raise FileNotFoundError(f"Call graph JSON not generated: {json_path}")

        logger.info(f"Call graph exported: {json_path} ({os.path.getsize(json_path)} bytes)")

    def _parse_call_graph_json(self, json_path: str) -> CallGraph:
        """Step 3: 解析 Joern 导出的 JSON, 构建 CallGraph."""
        with open(json_path, 'r') as f:
            data = json.load(f)

        cg = CallGraph()

        # 解析方法节点
        methods_data = data.get("methods", [])
        for m in methods_data:
            full_name = m["fullName"]
            method_name = m.get("name", "")

            # 用 fullName 标准化得到 node_id (去掉签名)
            node_id = self._full_name_to_node_id(full_name)

            # 从 node_id 中提取真正的 class_name
            # node_id 格式: "com.example.service.UserService.findById"
            parts = node_id.rsplit(".", 1)
            class_name = parts[0] if len(parts) == 2 else ""

            # 应用包过滤器
            if self.config.package_filter:
                if not class_name.startswith(self.config.package_filter):
                    continue

            # 排除 test
            if self.config.exclude_test:
                filename = m.get("filename", "")
                if "/test/" in filename or "/tests/" in filename:
                    continue

            node = CallGraphNode(
                class_name=class_name,
                method=method_name,
                method_signature=m.get("signature", ""),
                src_file=self._extract_filename(m.get("filename", "")),
                line_number=int(m.get("lineNumber", -1)),
            )
            cg.add_node(node)

        # 解析调用边
        calls_data = data.get("calls", [])
        node_ids = set(n for n in cg.nodes.keys())

        for c in calls_data:
            caller_full = c["caller"]
            callee_full = c["callee"]

            # 将 Joern fullName 转为我们的 node_id 格式 (className.methodName)
            caller_id = self._full_name_to_node_id(caller_full)
            callee_id = self._full_name_to_node_id(callee_full)

            # 只保留已注册的节点之间的边
            if caller_id in node_ids and callee_id in node_ids:
                edge = CallGraphEdge(
                    caller_id=caller_id,
                    callee_id=callee_id,
                    call_line=int(c.get("line", -1)),
                    is_taint=False,  # taint 由 CodeQL 后续标记
                )
                cg.add_edge(edge)

        cg.build_adjacency()

        logger.info(
            f"CallGraph built: {len(cg.nodes)} nodes, {len(cg.edges)} edges"
        )
        return cg

    def _full_name_to_node_id(self, full_name: str) -> str:
        """
        将 Joern 的 fullName 转为 CallGraph 的 node_id (className.methodName).

        Joern fullName 格式:
          - "com.example.service.UserService.findById:java.util.Map(java.lang.Long)"
          - "com.example.Controller.<init>:void()"

        我们的 node_id 格式:
          - "com.example.service.UserService.findById"
        """
        # 去掉返回类型+参数签名部分 (第一个冒号后的内容)
        if ":" in full_name:
            full_name = full_name.split(":")[0]

        # 去掉括号内容 (如果还有残留)
        if "(" in full_name:
            full_name = full_name.split("(")[0]

        return full_name

    def _extract_filename(self, filepath: str) -> str:
        """从完整路径提取文件名."""
        if not filepath:
            return ""
        return os.path.basename(filepath)


# ═══════════════════════════════════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════════════════════════════════

def generate_call_graph_from_source(
    source_dir: str,
    package_filter: str = "",
    joern_home: Optional[str] = None,
    jvm_memory: str = "4G",
) -> CallGraph:
    """
    便捷函数: 从 Java 源码目录生成调用图.

    Args:
        source_dir: Java 源码根目录
        package_filter: 包过滤器 (e.g. "com.example.microservice")
        joern_home: Joern 安装目录 (None = 从 PATH 查找)
        jvm_memory: JVM 最大内存

    Returns:
        CallGraph 对象, 可直接传给 PathGenerator

    Example:
        >>> cg = generate_call_graph_from_source(
        ...     "examples/java-microservice/src",
        ...     package_filter="com.example.microservice"
        ... )
        >>> print(f"{len(cg.nodes)} methods, {len(cg.edges)} call edges")
    """
    config = JoernConfig(
        joern_home=joern_home,
        package_filter=package_filter,
        jvm_memory=jvm_memory,
    )
    adapter = JoernAdapter(config=config)

    if not adapter.is_available():
        raise RuntimeError(
            "Joern not found. Install via:\n"
            "  curl -L https://github.com/joernio/joern/releases/latest/download/joern-install.sh | bash\n"
            "Or specify joern_home parameter."
        )

    return adapter.generate_call_graph(source_dir, package_filter)


# ═══════════════════════════════════════════════════════════════════════
# Docker 模式 (不需要本地安装 Joern)
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class JoernDockerAdapter:
    """
    使用 Docker 运行 Joern (无需本地安装).

    使用官方镜像: ghcr.io/joernio/joern
    """
    config: JoernConfig
    image: str = "ghcr.io/joernio/joern"

    def is_available(self) -> bool:
        """检查 Docker 是否可用."""
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True, text=True, timeout=10
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def generate_call_graph(self, source_dir: str,
                            package_filter: str = "") -> CallGraph:
        """使用 Docker 运行 Joern 生成调用图."""
        if package_filter:
            self.config.package_filter = package_filter

        source_dir = os.path.abspath(source_dir)

        with tempfile.TemporaryDirectory(prefix="joern_docker_") as tmpdir:
            script_path = os.path.join(tmpdir, "export_cg.sc")
            json_path = os.path.join(tmpdir, "callgraph.json")

            # 写入脚本
            with open(script_path, 'w') as f:
                f.write(EXPORT_CALL_GRAPH_SCRIPT_SIMPLE)

            # 在容器内执行: parse + query
            container_src = "/app/src"
            container_out = "/app/out"
            container_cpg = "/tmp/cpg.bin"
            container_json = f"{container_out}/callgraph.json"
            container_script = f"{container_out}/export_cg.sc"

            # 合并命令: parse → query
            inner_cmd = (
                f"joern-parse {container_src} --output {container_cpg} "
                f"--language {self.config.language} && "
                f"joern --script {container_script} "
                f"--param cpgFile={container_cpg} "
                f"--param outFile={container_json}"
            )

            cmd = [
                "docker", "run", "--rm",
                "-v", f"{source_dir}:{container_src}:ro",
                "-v", f"{tmpdir}:{container_out}",
                "-w", "/app",
                self.image,
                "bash", "-c", inner_cmd,
            ]

            logger.info(f"Running Joern in Docker: {self.image}")
            logger.debug(f"Command: {' '.join(cmd)}")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.config.parse_timeout + self.config.query_timeout,
            )

            if result.returncode != 0:
                raise RuntimeError(
                    f"Joern Docker failed (exit {result.returncode}):\n"
                    f"stdout: {result.stdout[:2000]}\n"
                    f"stderr: {result.stderr[:2000]}"
                )

            if not os.path.exists(json_path):
                raise FileNotFoundError(
                    f"Call graph JSON not generated. Docker output:\n"
                    f"{result.stdout[:1000]}"
                )

            # 复用本地解析逻辑
            local_adapter = JoernAdapter(config=self.config)
            return local_adapter._parse_call_graph_json(json_path)


# ═══════════════════════════════════════════════════════════════════════
# 从已有 JSON 加载 (缓存/离线模式)
# ═══════════════════════════════════════════════════════════════════════

def load_call_graph_from_json(
    json_path: str,
    package_filter: str = "",
) -> CallGraph:
    """
    从已导出的 Joern JSON 文件加载调用图.

    适用于: CPG 已生成并缓存的场景, 避免重复分析.

    Args:
        json_path: Joern 导出的 callgraph.json 路径
        package_filter: 包过滤器

    Returns:
        CallGraph 对象
    """
    config = JoernConfig(package_filter=package_filter)
    adapter = JoernAdapter(config=config)
    return adapter._parse_call_graph_json(json_path)
