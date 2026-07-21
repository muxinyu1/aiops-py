"""
api_discovery.py — Java Spring MVC API 入口自动发现模块

扫描 Java 源码目录，通过识别 Spring MVC 注解（@RestController/@Controller +
@GetMapping/@PostMapping/@RequestMapping 等）自动提取所有 REST API 入口方法。

输出与 expected_path.py 中的 APIEntry 数据类兼容。

用法::

    from api_discovery import discover_api_entries

    entries = discover_api_entries("examples/java-microservice")
    for e in entries:
        print(f"{e.http_method} {e.http_path}  →  {e.class_name}.{e.method}")
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from expected_path import APIEntry


# ═══════════════════════════════════════════════════════════════════════════════
# Constants: Spring MVC annotation patterns
# ═══════════════════════════════════════════════════════════════════════════════

# Class-level markers that identify a controller
_CONTROLLER_ANNOTATIONS = {"RestController", "Controller"}

# Method-level HTTP verb annotations → default HTTP method
_VERB_ANNOTATIONS: dict[str, str] = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "DeleteMapping": "DELETE",
    "PatchMapping": "PATCH",
}

# RequestMapping method= enum values
_REQUEST_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "TRACE"}


# ═══════════════════════════════════════════════════════════════════════════════
# Regex patterns for Java source parsing
# ═══════════════════════════════════════════════════════════════════════════════

# Match package declaration
_RE_PACKAGE = re.compile(r'^\s*package\s+([\w.]+)\s*;', re.MULTILINE)

# Match class/interface declaration (captures class name)
_RE_CLASS_DECL = re.compile(
    r'(?:public\s+)?(?:abstract\s+)?(?:class|interface)\s+(\w+)'
)

# Match any annotation: @Name or @Name(...) — captures name and optional args
# Handles multi-line annotations
_RE_ANNOTATION = re.compile(
    r'@(\w+)(?:\s*\(([^)]*(?:\([^)]*\)[^)]*)*)\))?'
)

# Match method declaration (simplified — captures return type + name + params)
_RE_METHOD_DECL = re.compile(
    r'(?:public|protected|private)?\s*'
    r'(?:static\s+)?'
    r'(?:default\s+)?'
    r'(?:[\w<>\[\],\s?]+?)\s+'  # return type
    r'(\w+)'                     # method name
    r'\s*\('                     # opening paren
)

# Extract string literals from annotation args
_RE_STRING_LITERAL = re.compile(r'"([^"]*)"')

# Extract method= from @RequestMapping
_RE_METHOD_ATTR = re.compile(
    r'method\s*=\s*\{?\s*((?:RequestMethod\.\w+\s*,?\s*)+)\}?'
)

# Extract value=/path= from mapping annotations
_RE_VALUE_ATTR = re.compile(
    r'(?:value|path)\s*=\s*\{?\s*("(?:[^"]*)"(?:\s*,\s*"[^"]*")*)\s*\}?'
)

# Extract RequestMethod.XXX
_RE_REQUEST_METHOD = re.compile(r'RequestMethod\.(\w+)')


# ═══════════════════════════════════════════════════════════════════════════════
# Data structures
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class _AnnotationInfo:
    """Parsed annotation."""
    name: str
    raw_args: str = ""

    def string_values(self) -> list[str]:
        """Extract all string literal values from annotation args."""
        return _RE_STRING_LITERAL.findall(self.raw_args)

    def http_methods(self) -> list[str]:
        """Extract RequestMethod.XXX values."""
        return _RE_REQUEST_METHOD.findall(self.raw_args)

    def path_values(self) -> list[str]:
        """Extract value= or path= attribute, or bare string values."""
        # Try explicit value=/path= first
        m = _RE_VALUE_ATTR.search(self.raw_args)
        if m:
            return _RE_STRING_LITERAL.findall(m.group(1))
        # Fall back to bare string literals (first positional arg)
        # e.g. @GetMapping("/users") or @RequestMapping("/base")
        strings = self.string_values()
        # Filter out non-path strings (those containing = are likely other attrs)
        if strings:
            # If the raw_args has key= patterns, only take the first bare string
            if '=' in self.raw_args:
                # Check if there's a bare string before the first key=
                bare_match = re.match(r'\s*"([^"]*)"', self.raw_args)
                if bare_match:
                    return [bare_match.group(1)]
                return []
            return strings
        return []


@dataclass
class _MethodInfo:
    """Parsed method with its annotations."""
    name: str
    line_number: int
    annotations: list[_AnnotationInfo] = field(default_factory=list)


@dataclass
class _ClassInfo:
    """Parsed class with its annotations and methods."""
    name: str
    package: str
    src_file: str
    line_number: int
    annotations: list[_AnnotationInfo] = field(default_factory=list)
    methods: list[_MethodInfo] = field(default_factory=list)

    @property
    def qualified_name(self) -> str:
        if self.package:
            return f"{self.package}.{self.name}"
        return self.name

    def is_controller(self) -> bool:
        """Check if this class is annotated as a Spring controller."""
        return any(a.name in _CONTROLLER_ANNOTATIONS for a in self.annotations)

    def base_paths(self) -> list[str]:
        """Get class-level @RequestMapping path(s)."""
        for a in self.annotations:
            if a.name == "RequestMapping":
                paths = a.path_values()
                if paths:
                    return paths
        return [""]


# ═══════════════════════════════════════════════════════════════════════════════
# Parser
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_java_file(filepath: str) -> list[_ClassInfo]:
    """
    Parse a Java source file and extract class + method + annotation info.

    This is a lightweight regex-based parser (not a full Java AST parser).
    It handles the common Spring MVC patterns reliably.
    """
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except (OSError, IOError):
        return []

    # Extract package
    pkg_match = _RE_PACKAGE.search(content)
    package = pkg_match.group(1) if pkg_match else ""

    classes: list[_ClassInfo] = []
    lines = content.split('\n')

    current_class: Optional[_ClassInfo] = None
    pending_annotations: list[_AnnotationInfo] = []
    brace_depth = 0
    class_brace_depth = -1

    # Track multi-line annotation state
    in_multiline_annotation = False
    multiline_name = ""
    multiline_args = ""
    paren_depth = 0

    for line_idx, line in enumerate(lines):
        line_no = line_idx + 1
        stripped = line.strip()

        # Skip comments and empty lines
        if stripped.startswith("//") or stripped.startswith("*") or stripped.startswith("/*"):
            continue

        # Handle multi-line annotation continuation
        if in_multiline_annotation:
            multiline_args += " " + stripped
            paren_depth += stripped.count('(') - stripped.count(')')
            if paren_depth <= 0:
                in_multiline_annotation = False
                # Clean up the args - remove the outer parens
                args_clean = multiline_args.strip()
                pending_annotations.append(_AnnotationInfo(
                    name=multiline_name, raw_args=args_clean
                ))
                multiline_name = ""
                multiline_args = ""
            continue

        # Track brace depth for class scope
        brace_depth += stripped.count('{') - stripped.count('}')

        # Detect class exit
        if current_class and brace_depth <= class_brace_depth:
            classes.append(current_class)
            current_class = None
            class_brace_depth = -1
            pending_annotations = []

        # Detect annotations
        for m in _RE_ANNOTATION.finditer(line):
            ann_name = m.group(1)
            ann_args = m.group(2) or ""

            # Check if annotation is complete (balanced parens)
            if '(' in line[m.start():] and ann_args == "" and m.group(0).count('(') > m.group(0).count(')'):
                # Multi-line annotation — start accumulating
                in_multiline_annotation = True
                multiline_name = ann_name
                # Grab whatever is after the opening paren on this line
                paren_start = line.index('(', m.start())
                multiline_args = line[paren_start + 1:]
                paren_depth = multiline_args.count('(') - multiline_args.count(')') + 1  # +1 for the opening
                if paren_depth <= 0:
                    in_multiline_annotation = False
                    pending_annotations.append(_AnnotationInfo(name=ann_name, raw_args=multiline_args.rstrip(')').strip()))
                    multiline_args = ""
                continue

            # Filter: only keep relevant annotations
            if ann_name in _CONTROLLER_ANNOTATIONS or ann_name == "RequestMapping" or ann_name in _VERB_ANNOTATIONS:
                pending_annotations.append(_AnnotationInfo(name=ann_name, raw_args=ann_args))

        # Detect class declaration
        if not current_class:
            class_match = _RE_CLASS_DECL.search(stripped)
            if class_match and not stripped.startswith("//") and '{' in stripped or (class_match and line_idx + 1 < len(lines)):
                # Verify it's a real class decl (has { on this or next line)
                if '{' in stripped or (line_idx + 1 < len(lines) and '{' in lines[line_idx + 1]):
                    class_name = class_match.group(1)
                    current_class = _ClassInfo(
                        name=class_name,
                        package=package,
                        src_file=filepath,
                        line_number=line_no,
                        annotations=pending_annotations,
                    )
                    class_brace_depth = brace_depth - (1 if '{' in stripped else 0)
                    pending_annotations = []
                    continue

        # Detect method declaration (only inside a class)
        if current_class and pending_annotations:
            method_match = _RE_METHOD_DECL.search(stripped)
            if method_match:
                method_name = method_match.group(1)
                # Skip constructors and common non-endpoint methods
                if method_name != current_class.name and method_name not in (
                    "main", "toString", "hashCode", "equals", "clone"
                ):
                    current_class.methods.append(_MethodInfo(
                        name=method_name,
                        line_number=line_no,
                        annotations=pending_annotations,
                    ))
                pending_annotations = []
        elif current_class and not stripped.startswith("@"):
            # Not an annotation line and no pending annotations — check for method anyway
            # (method with no mapping annotations, just clear pending state)
            if _RE_METHOD_DECL.search(stripped):
                pending_annotations = []

    # Handle last class
    if current_class:
        classes.append(current_class)

    return classes


# ═══════════════════════════════════════════════════════════════════════════════
# API Entry extraction
# ═══════════════════════════════════════════════════════════════════════════════

def _normalize_path(base: str, method_path: str) -> str:
    """Join class-level base path with method-level path."""
    # Remove trailing/leading slashes for clean join
    base = base.rstrip("/")
    method_path = method_path.lstrip("/") if method_path else ""

    if base and method_path:
        return f"/{base.lstrip('/')}/{method_path}"
    elif base:
        return f"/{base.lstrip('/')}"
    elif method_path:
        return f"/{method_path}"
    else:
        return "/"


def _extract_entries_from_class(cls: _ClassInfo) -> list[APIEntry]:
    """Extract APIEntry objects from a parsed controller class."""
    if not cls.is_controller():
        return []

    base_paths = cls.base_paths()
    entries: list[APIEntry] = []

    for method in cls.methods:
        http_methods: list[str] = []
        method_paths: list[str] = []

        for ann in method.annotations:
            if ann.name in _VERB_ANNOTATIONS:
                # @GetMapping, @PostMapping, etc.
                http_methods.append(_VERB_ANNOTATIONS[ann.name])
                paths = ann.path_values()
                if paths:
                    method_paths.extend(paths)
                else:
                    method_paths.append("")
            elif ann.name == "RequestMapping":
                # @RequestMapping — check for method= attribute
                req_methods = ann.http_methods()
                if req_methods:
                    http_methods.extend(m.upper() for m in req_methods)
                else:
                    # No method= means ALL methods, default to GET
                    http_methods.append("GET")
                paths = ann.path_values()
                if paths:
                    method_paths.extend(paths)
                else:
                    method_paths.append("")

        if not http_methods:
            continue

        # Deduplicate
        if not method_paths:
            method_paths = [""]

        # Generate one APIEntry per (http_method, base_path, method_path) combination
        seen: set[str] = set()
        for http_method in http_methods:
            for base in base_paths:
                for mpath in method_paths:
                    full_path = _normalize_path(base, mpath)
                    key = f"{http_method} {full_path}"
                    if key in seen:
                        continue
                    seen.add(key)

                    entries.append(APIEntry(
                        class_name=cls.qualified_name,
                        method=method.name,
                        src_file=cls.src_file,
                        line_number=method.line_number,
                        http_method=http_method,
                        http_path=full_path,
                    ))

    return entries


# ═══════════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════════

def discover_api_entries(
    source_dir: str,
    *,
    package_filter: str = "",
    exclude_patterns: Optional[list[str]] = None,
) -> list[APIEntry]:
    """
    扫描 Java 源码目录，发现所有 Spring MVC REST API 入口方法。

    Args:
        source_dir: Java 项目源码根目录（会递归扫描所有 .java 文件）
        package_filter: 只保留匹配此包前缀的 controller（留空则全部保留）
        exclude_patterns: 排除的路径模式列表（如 ["test", "generated"]）

    Returns:
        list[APIEntry]: 发现的所有 API 入口点
    """
    source_path = Path(source_dir).resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"Source directory not found: {source_dir}")

    if exclude_patterns is None:
        exclude_patterns = ["test", "generated", "target"]

    entries: list[APIEntry] = []

    # Find all .java files
    for java_file in source_path.rglob("*.java"):
        # Skip excluded paths
        rel_path = str(java_file.relative_to(source_path))
        if any(pat in rel_path.lower() for pat in exclude_patterns):
            continue

        # Parse the file
        classes = _parse_java_file(str(java_file))

        for cls in classes:
            # Apply package filter
            if package_filter and not cls.qualified_name.startswith(package_filter):
                continue

            # Extract API entries
            cls_entries = _extract_entries_from_class(cls)
            entries.extend(cls_entries)

    # Sort by (class_name, method_name, http_method) for deterministic output
    entries.sort(key=lambda e: (e.class_name, e.method, e.http_method))
    return entries


def discover_all_projects(
    examples_dir: str = "examples",
    *,
    exclude_projects: Optional[list[str]] = None,
) -> dict[str, list[APIEntry]]:
    """
    扫描 examples/ 下所有项目，返回每个项目的 API 入口。

    Args:
        examples_dir: examples 目录路径
        exclude_projects: 要跳过的项目名列表

    Returns:
        dict[project_name, list[APIEntry]]: 各项目的 API 入口
    """
    examples_path = Path(examples_dir).resolve()
    if not examples_path.exists():
        raise FileNotFoundError(f"Examples directory not found: {examples_dir}")

    if exclude_projects is None:
        exclude_projects = []

    # Known project → package mappings for targeted discovery
    _PROJECT_PACKAGES: dict[str, str] = {
        "java-microservice": "com.example.microservice",
        "pig": "com.pig4cloud.pig",
        "RuoYi-Cloud": "com.ruoyi",
        "RuoYi-Cloud-Plus": "org.dromara",
        "mall-swarm": "com.macro.mall",
        "SpringBlade": "org.springblade",
        "youlai-mall": "com.youlai",
        "mall4cloud": "com.mall4j.cloud",
        "zlt-microservices-platform": "com.central",
        "Apollo": "com.ctrip.framework.apollo",
        "novel-cloud": "io.github.xxyopen.novel",
        "yudao-cloud": "cn.iocoder.yudao",
        "PiggyMetrics": "com.piggymetrics",
        "MoGuBlog": "com.moxi.mogublog",
        "Cloud-Platform": "com.github.wxiaoqi",
        "PassJava-Platform": "com.jackson0714.passjava",
        "gulimall-learning": "io.niceseason.gulimall",
        "lamp-cloud": "top.tangyh.lamp",
    }

    results: dict[str, list[APIEntry]] = {}

    for project_dir in sorted(examples_path.iterdir()):
        if not project_dir.is_dir():
            continue
        project_name = project_dir.name
        if project_name in exclude_projects:
            continue

        # Use known package filter if available
        pkg_filter = _PROJECT_PACKAGES.get(project_name, "")

        entries = discover_api_entries(
            str(project_dir),
            package_filter=pkg_filter,
        )

        if entries:
            results[project_name] = entries

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def _print_summary(results: dict[str, list[APIEntry]]) -> None:
    """Print a readable summary of discovered API entries."""
    total = sum(len(v) for v in results.values())
    print(f"\n{'═' * 70}")
    print(f"API Discovery Summary: {total} endpoints across {len(results)} projects")
    print(f"{'═' * 70}\n")

    for project, entries in sorted(results.items()):
        print(f"📦 {project} ({len(entries)} endpoints)")
        for entry in entries[:20]:  # Show max 20 per project
            print(f"   {entry.http_method:7s} {entry.http_path}")
            print(f"           → {entry.class_name}.{entry.method}()")
        if len(entries) > 20:
            print(f"   ... and {len(entries) - 20} more")
        print()


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        # Single project mode
        target = sys.argv[1]
        pkg = sys.argv[2] if len(sys.argv) > 2 else ""
        entries = discover_api_entries(target, package_filter=pkg)
        _print_summary({Path(target).name: entries})
    else:
        # Discover all projects under examples/
        results = discover_all_projects("examples")
        _print_summary(results)
