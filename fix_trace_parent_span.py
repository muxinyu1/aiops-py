#!/usr/bin/env python3
"""
批量修复所有项目的 TracingAspect 和 TraceContextHolder，
让它们正确维护 parent_span_id 关系。
"""

import re
import sys
from pathlib import Path

# TraceContextHolder 的旧版本模式
OLD_HOLDER_PATTERN = re.compile(
    r'(package [\w.]+;\s*\n\s*\n)'
    r'public final class TraceContextHolder \{\s*\n'
    r'\s*private static final ThreadLocal<String> TRACE_ID = new ThreadLocal<>\(\);\s*\n'
    r'\s*private TraceContextHolder\(\) \{\}\s*\n'
    r'\s*public static void set\(String traceId\)\s+\{ TRACE_ID\.set\(traceId\); \}\s*\n'
    r'\s*public static String get\(\)\s+\{ return TRACE_ID\.get\(\); \}\s*\n'
    r'\s*public static void clear\(\)\s+\{ TRACE_ID\.remove\(\); \}\s*\n'
    r'\}',
    re.MULTILINE
)

# TracingAspect 的旧版本模式（parent_span_id = ""）
OLD_ASPECT_PATTERN = re.compile(
    r'(\s+String spanId\s+=\s+UUID\.randomUUID\(\)\.toString\(\)\.replace\("-", ""\)\.substring\(0, 16\);\s*\n)'
    r'(\s+long\s+epochNs\s+=.*?\n)'
    r'(\s+long\s+startNs\s+=.*?\n)'
    r'(\s+boolean isError\s+=.*?\n)'
    r'(\s+String\s+errorMsg\s+=.*?\n)'
    r'(\s+\n)'
    r'(\s+try \{\s*\n)'
    r'(\s+return pjp\.proceed\(\);\s*\n)'
    r'(\s+\} catch \(Throwable ex\) \{\s*\n)'
    r'(.*?)'
    r'(\s+\} finally \{\s*\n)'
    r'(\s+long durationNs = System\.nanoTime\(\) - startNs;\s*\n)'
    r'(\s+SpanRecord record\s+=.*?\n)'
    r'(\s+record\.span_id\s+=.*?\n)'
    r'(\s+record\.parent_span_id\s+=\s+"";)',
    re.MULTILINE | re.DOTALL
)

NEW_HOLDER_CONTENT = """package {package};

import java.util.ArrayDeque;
import java.util.Deque;

public final class TraceContextHolder {{
    private static final ThreadLocal<String> TRACE_ID = new ThreadLocal<>();
    private static final ThreadLocal<Deque<String>> SPAN_STACK =
            ThreadLocal.withInitial(ArrayDeque::new);

    private TraceContextHolder() {{}}

    public static void set(String traceId)  {{ TRACE_ID.set(traceId); }}
    public static String get()              {{ return TRACE_ID.get(); }}
    public static void clear() {{
        TRACE_ID.remove();
        SPAN_STACK.remove();
    }}

    /** Get current parent span ID (top of stack), or empty string if root. */
    public static String currentParentSpanId() {{
        Deque<String> stack = SPAN_STACK.get();
        return stack.isEmpty() ? "" : stack.peek();
    }}

    /** Push span ID onto stack (entering a method). */
    public static void pushSpan(String spanId) {{
        SPAN_STACK.get().push(spanId);
    }}

    /** Pop span ID from stack (exiting a method). */
    public static void popSpan() {{
        Deque<String> stack = SPAN_STACK.get();
        if (!stack.isEmpty()) {{
            stack.pop();
        }}
    }}
}}
"""


def fix_trace_context_holder(file_path: Path) -> bool:
    """修复 TraceContextHolder.java，加入 span 栈。"""
    content = file_path.read_text(encoding='utf-8')
    
    # 提取 package 声明
    pkg_match = re.search(r'^package ([\w.]+);', content, re.MULTILINE)
    if not pkg_match:
        print(f"  ⚠️  Cannot find package declaration in {file_path}")
        return False
    
    package = pkg_match.group(1)
    
    # 检查是否已经修复过
    if 'pushSpan' in content or 'SPAN_STACK' in content:
        print(f"  ✓ Already fixed: {file_path}")
        return False
    
    # 检查是否匹配旧模式
    if not OLD_HOLDER_PATTERN.search(content):
        print(f"  ⚠️  Pattern mismatch (may use different implementation): {file_path}")
        return False
    
    # 替换为新内容
    new_content = NEW_HOLDER_CONTENT.format(package=package)
    file_path.write_text(new_content, encoding='utf-8')
    print(f"  ✓ Fixed: {file_path}")
    return True


def fix_tracing_aspect(file_path: Path) -> bool:
    """修复 TracingAspect.java，使用栈维护 parent_span_id。"""
    content = file_path.read_text(encoding='utf-8')
    
    # 检查是否已经修复过
    if 'TraceContextHolder.currentParentSpanId()' in content or 'TraceContextHolder.pushSpan' in content:
        print(f"  ✓ Already fixed: {file_path}")
        return False
    
    # 检查是否有硬编码的空 parent_span_id
    if 'parent_span_id   = "";' not in content and 'parent_span_id = "";' not in content:
        print(f"  ⚠️  No hardcoded empty parent_span_id found (may use different pattern): {file_path}")
        return False
    
    # 模式1：parent_span_id   = ""; (多空格)
    pattern1 = re.compile(
        r'(\s+String spanId\s+=\s+UUID\.randomUUID\(\)[^\n]+\n)'
        r'(\s+long\s+epochNs[^\n]+\n)'
        r'(\s+long\s+startNs[^\n]+\n)'
        r'(\s+boolean isError[^\n]+\n)'
        r'(\s+String\s+errorMsg[^\n]+\n)'
        r'(\s*\n)'
        r'(\s+try \{[^\n]*\n)'
        r'(\s+return pjp\.proceed\(\);[^\n]*\n)'
        r'(\s+\} catch[^\n]+\n)'
        r'((?:.|\n)*?)'
        r'(\s+\} finally \{[^\n]*\n)'
        r'(\s+long durationNs[^\n]+\n)'
        r'(\s+SpanRecord record[^\n]+\n)'
        r'(\s+record\.span_id[^\n]+\n)'
        r'(\s+record\.parent_span_id\s+=\s+"";)',
        re.MULTILINE
    )
    
    def replacement(m):
        indent = ' ' * 8  # 假设是 8 空格缩进
        parent_line = m.group(15).replace('""', 'parentSpanId')
        parts = [
            m.group(1),
            f"{indent}// Get parent before pushing self onto stack\n",
            f"{indent}String parentSpanId = TraceContextHolder.currentParentSpanId();\n",
            f"{indent}TraceContextHolder.pushSpan(spanId);\n",
            "\n",
            m.group(2),
            m.group(3),
            m.group(4),
            m.group(5),
            m.group(6),
            m.group(7),
            m.group(8),
            m.group(9),
            m.group(10),
            m.group(11),
            f"{indent}TraceContextHolder.popSpan();\n",
            m.group(12),
            m.group(13),
            m.group(14),
            parent_line,
        ]
        return "".join(parts)
    
    new_content, count = pattern1.subn(replacement, content)
    
    if count == 0:
        print(f"  ⚠️  Pattern match failed: {file_path}")
        return False
    
    file_path.write_text(new_content, encoding='utf-8')
    print(f"  ✓ Fixed: {file_path}")
    return True


def main():
    examples_dir = Path("/home/mxy/Documents/aiops-py/examples")
    
    # 找到所有需要修复的文件（排除 PiggyMetrics 和 java-microservice）
    holder_files = list(examples_dir.rglob("**/tracing/TraceContextHolder.java"))
    aspect_files = list(examples_dir.rglob("**/tracing/TracingAspect.java"))
    
    # 排除已修复的项目
    exclude_projects = {'PiggyMetrics', 'java-microservice'}
    holder_files = [f for f in holder_files if not any(p in str(f) for p in exclude_projects)]
    aspect_files = [f for f in aspect_files if not any(p in str(f) for p in exclude_projects)]
    
    print(f"Found {len(holder_files)} TraceContextHolder.java files to fix")
    print(f"Found {len(aspect_files)} TracingAspect.java files to fix\n")
    
    print("=== Fixing TraceContextHolder.java ===")
    holder_fixed = 0
    for f in holder_files:
        if fix_trace_context_holder(f):
            holder_fixed += 1
    
    print(f"\n=== Fixing TracingAspect.java ===")
    aspect_fixed = 0
    for f in aspect_files:
        if fix_tracing_aspect(f):
            aspect_fixed += 1
    
    print(f"\n=== Summary ===")
    print(f"TraceContextHolder: {holder_fixed}/{len(holder_files)} fixed")
    print(f"TracingAspect: {aspect_fixed}/{len(aspect_files)} fixed")


if __name__ == "__main__":
    main()
