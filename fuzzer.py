"""
fuzzer.py — LLM 驱动的 Sink-Centric Fuzz Agent

核心职责：
  1. 构造 system prompt（目标 sink、API 信息、参数类型等）
  2. 构造 user prompt（偏差反馈 + 历史失败请求）
  3. 调用 LLM 生成 HTTP 请求参数
  4. 解析 LLM 输出为 HttpParameter

设计参考 SP 2026 (GONDAR) 的 Exploration Agent：
  - 给模型展示从 API entry 到 target sink 的预期路径
  - 每次尝试后，将实际执行偏差反馈给模型
  - 模型根据偏差调整参数，逐步逼近 sink
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from expected_path import APIEntry, ExpectedPath, PathNode
from llm import LLM, Message
from parameter import HttpParameter
from path_differ import PathDivergence
from sink import Sink


# ═══════════════════════════════════════════════════════════════════════════════
# Prompt 模板
# ═══════════════════════════════════════════════════════════════════════════════

_SYSTEM_PROMPT_TEMPLATE = """\
你是一个安全测试专家，正在对一个 Java 微服务进行 Log Injection 攻击测试。

## 攻击目标

你需要构造 HTTP 请求，使得：
1. 请求的执行路径到达目标日志打印点（sink）
2. **日志打印的内容中包含攻击标记: `{attack_marker}`**

你的输入会通过参数传递到日志语句中。你需要让 `{attack_marker}` 出现在应用日志输出里。

**目标 Sink**: `{sink_class}.{sink_method}` (第 {sink_line} 行)
- 日志级别: {sink_type}
- 日志模板: `{sink_message}`
- 污染参数: {tainted_params}

分析日志模板中的 `{{}}` 占位符，它们对应从请求参数中流入的值。你需要将 `{attack_marker}` 注入到这些参数中，使其被日志打印出来。

## API 入口

你要调用的 API：
- HTTP 方法: {http_method}
- 路径: {http_path}
- 完整 URL: {base_url}{http_path}
{param_info}

## 预期执行路径

从 API 入口到目标 sink 的预期调用链：
{expected_path_str}

## 输出格式

请严格按照以下格式输出，先在 <think> 标签中分析推理，再在 <json> 标签中输出请求：

<think>
你的思考过程：
1. 分析哪个请求参数会流入日志模板的占位符
2. 如何构造参数值使其包含攻击标记 "{attack_marker}"
3. 如何满足路径约束使执行到达 sink 点
</think>
<json>
{{
  "method": "GET 或 POST 等",
  "url": "完整 URL（包括路径参数的具体值）",
  "headers": {{"Header-Name": "value"}},
  "body": null 或 JSON 对象
}}
</json>

## 重要提示

1. URL 中的路径参数（如 `{{id}}`）必须替换为具体值
2. POST/PUT 请求需要提供 JSON body
3. **核心目标: 让应用日志中出现 `{attack_marker}` 字符串**
4. 攻击标记必须通过请求参数注入，不能在 header 中伪造
5. 分析污染参数列表，选择正确的参数注入攻击标记
6. <json> 标签内只放纯 JSON，不要有其他内容
"""

_USER_PROMPT_FIRST = """\
请构造第一个请求来尝试攻击目标 sink。

分析：
1. 日志模板中的 `{}` 占位符对应哪个请求参数
2. 如何让请求参数携带攻击标记到达该日志打印点
3. 需要什么条件才能走到 sink 所在的代码分支
"""

_USER_PROMPT_WITH_FEEDBACK = """\
上一次攻击未成功。以下是分析：

## 第 {attempt_num} 次尝试结果

**发送的请求**: {prev_request}

**到达 sink**: {reached_sink}
**日志中出现攻击标记**: {marker_found}

**执行偏差**:
{divergence_info}

## 历史失败请求
{history_summary}

## 要求

根据以上信息调整策略：
- 如果未到达 sink: 分析执行偏差，调整参数使执行路径到达 sink
- 如果到达 sink 但标记未出现: 说明参数没有流入日志，需要换一个参数注入攻击标记
- 不要重复之前失败的请求

请输出新的请求 JSON。
"""


# ═══════════════════════════════════════════════════════════════════════════════
# Fuzz Agent
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class FuzzAttempt:
    """一次 fuzz 尝试的记录."""
    request: HttpParameter
    divergence: Optional[PathDivergence] = None
    reached_sink: bool = False
    marker_found: bool = False  # 攻击标记是否出现在日志中


@dataclass
class Fuzzer:
    """
    LLM 驱动的 Log Injection Fuzz Agent。

    对于一条预期路径 (API entry → sink)，通过多轮 LLM 对话
    迭代生成请求参数，目标是让攻击标记出现在目标日志输出中。
    """

    llm: LLM
    base_url: str = "http://localhost:8080"  # 目标服务地址
    attack_marker: str = "sink_attacked"  # 攻击标记字符串
    source_root: str = ""  # 项目源码根目录（用于读取偏差点源码）

    def build_system_prompt(
        self,
        api_entry: APIEntry,
        sink: Sink,
        expected_path: ExpectedPath,
    ) -> str:
        """构造 system prompt，描述攻击目标、API 和预期路径."""

        # 参数信息
        param_info = self._build_param_info(api_entry)

        # 预期路径描述
        path_nodes = expected_path.nodes if expected_path.nodes else []
        expected_path_str = " → ".join(
            f"`{node.class_name.split('.')[-1]}.{node.method}`"
            for node in path_nodes
        ) if path_nodes else "(无详细路径信息)"

        # 污染参数
        tainted = ", ".join(sink.tainted_params) if sink.tainted_params else "(需根据日志模板推断)"

        # 路径参数替换提示
        http_path = api_entry.http_path

        return _SYSTEM_PROMPT_TEMPLATE.format(
            attack_marker=self.attack_marker,
            sink_class=sink.class_name.split('.')[-1],
            sink_method=sink.method,
            sink_line=sink.line_number,
            sink_type=sink.sink_type.value if hasattr(sink.sink_type, 'value') else str(sink.sink_type),
            sink_message=sink.log_message or "(未知)",
            tainted_params=tainted,
            http_method=api_entry.http_method,
            http_path=http_path,
            base_url=self.base_url,
            param_info=param_info,
            expected_path_str=expected_path_str,
        )

    def build_user_prompt(
        self,
        history: list[FuzzAttempt],
    ) -> str:
        """构造 user prompt，包含攻击反馈和历史失败请求."""

        if not history:
            return _USER_PROMPT_FIRST

        last = history[-1]
        attempt_num = len(history)

        # 上一次请求描述
        prev_request = self._format_request(last.request)

        # 偏差描述
        divergence_info = self._format_divergence(last.divergence)

        # 攻击结果
        reached_sink = "✅ 是" if last.reached_sink else "❌ 否"
        marker_found = "✅ 是" if last.marker_found else "❌ 否"

        # 历史摘要（最多展示最近 5 次）
        recent = history[-5:]
        history_lines = []
        for i, attempt in enumerate(recent, 1):
            req_desc = self._format_request(attempt.request)
            sink_icon = "✓" if attempt.reached_sink else "✗"
            marker_icon = "✓" if attempt.marker_found else "✗"
            history_lines.append(f"  尝试 {i}: {req_desc} → sink:{sink_icon} marker:{marker_icon}")
        history_summary = "\n".join(history_lines)

        return _USER_PROMPT_WITH_FEEDBACK.format(
            attempt_num=attempt_num,
            prev_request=prev_request,
            reached_sink=reached_sink,
            marker_found=marker_found,
            divergence_info=divergence_info,
            history_summary=history_summary,
        )

    def fuzz(
        self,
        api_entry: APIEntry,
        sink: Sink,
        expected_path: ExpectedPath,
        history: list[FuzzAttempt],
    ) -> HttpParameter:
        """
        调用 LLM 生成一次 HTTP 请求参数。

        Args:
            api_entry: API 入口信息
            sink: 目标 sink
            expected_path: 预期执行路径
            history: 历史尝试记录（含偏差反馈）

        Returns:
            HttpParameter: LLM 生成的 HTTP 请求参数
        """
        system_prompt = self.build_system_prompt(api_entry, sink, expected_path)
        user_prompt = self.build_user_prompt(history)

        # 构造多轮对话 messages
        messages = [Message(role="system", content=system_prompt)]

        # 把历史对话也加入 context（让 LLM 看到之前的尝试）
        for attempt in history[-3:]:  # 最多保留最近 3 轮
            # 模拟之前的 user prompt
            messages.append(Message(
                role="assistant",
                content=self._format_request_as_json(attempt.request),
            ))
            if attempt.divergence:
                messages.append(Message(
                    role="user",
                    content=f"请求未能到达 sink。偏差: {attempt.divergence.summary}\n请调整参数重试。",
                ))

        # 当前轮的 user prompt
        messages.append(Message(role="user", content=user_prompt))

        # 调用 LLM
        response = self.llm.chat(messages)

        # 解析 LLM 输出为 HttpParameter
        return self._parse_response(response, api_entry)

    # ── 辅助方法 ─────────────────────────────────────────────────────────────

    def _build_param_info(self, api_entry: APIEntry) -> str:
        """根据 API 信息推断参数描述."""
        lines = []

        # 路径参数
        path_params = re.findall(r'\{(\w+)\}', api_entry.http_path)
        if path_params:
            lines.append(f"- 路径参数: {', '.join(path_params)}")

        # HTTP method 暗示
        if api_entry.http_method in ("POST", "PUT", "PATCH"):
            lines.append("- 请求体: JSON (具体字段需要根据代码推断)")
        elif api_entry.http_method == "GET":
            lines.append("- 查询参数: 可能有 (具体需要根据代码推断)")

        return "\n".join(lines) if lines else "- 参数信息: 需根据路径和方法推断"

    def _format_request(self, param: HttpParameter) -> str:
        """将 HttpParameter 格式化为可读字符串."""
        s = f"{param.method} {param.url}"
        if param.body:
            s += f" body={param.body[:200]}"
        return s

    def _format_request_as_json(self, param: HttpParameter) -> str:
        """将 HttpParameter 转为 <think>+<json> 格式（模拟 LLM 之前的输出）."""
        obj = {
            "method": param.method,
            "url": param.url,
            "headers": param.headers,
            "body": json.loads(param.body) if param.body else None,
        }
        return f"<think>\n(之前的推理过程)\n</think>\n<json>\n{json.dumps(obj, indent=2, ensure_ascii=False)}\n</json>"

    def _format_divergence(self, div: Optional[PathDivergence]) -> str:
        """将 PathDivergence 格式化为可读的偏差描述，包含源码和参数值."""
        if not div:
            return "无偏差数据（可能请求执行失败或 trace 为空）"

        lines = []
        lines.append(f"- 偏差类型: {div.divergence_reason}")
        lines.append(f"- 到达进度: {div.reached_depth + 1}/{div.expected_path.path_length} 个节点")

        if div.reached_node:
            lines.append(f"- 最后到达: `{div.reached_node.qualified_name}`")
        if div.first_missed_node:
            lines.append(f"- 第一个未到达: `{div.first_missed_node.qualified_name}`")
        if div.actual_path_sequence:
            actual = " → ".join(div.actual_path_sequence[:10])
            lines.append(f"- 实际执行路径: {actual}")

        # ── 偏差点方法的输入参数值 ──
        if div.matched_trace_node and div.matched_trace_node.args_snapshot:
            lines.append("")
            lines.append("**偏差点方法输入参数:**")
            lines.append(f"```json")
            lines.append(json.dumps(div.matched_trace_node.args_snapshot, ensure_ascii=False, indent=2))
            lines.append(f"```")

        # ── 偏差点方法的源码 ──
        divergence_node = div.first_missed_node or div.reached_node
        if divergence_node and self.source_root:
            source_snippet = self._read_method_source(divergence_node)
            if source_snippet:
                lines.append("")
                lines.append(f"**偏差点方法源码** (`{divergence_node.qualified_name}`):")
                lines.append("```java")
                lines.append(source_snippet)
                lines.append("```")

        return "\n".join(lines)

    def _read_method_source(self, node: "PathNode") -> str:
        """
        根据 PathNode 定位源码文件，读取对应方法的源码片段。

        查找策略：在 source_root 下递归查找匹配 class_namespace 路径的 .java 文件。
        """
        import os
        import glob

        if not self.source_root or not os.path.isdir(self.source_root):
            return ""

        # 将 class_namespace 转为路径：com.example.Foo → com/example/Foo.java
        class_path = node.class_name.replace(".", "/") + ".java"

        # 在 source_root 下查找
        pattern = os.path.join(self.source_root, "**", class_path)
        matches = glob.glob(pattern, recursive=True)

        if not matches:
            # 尝试只用文件名
            simple_name = node.class_name.split(".")[-1] + ".java"
            pattern = os.path.join(self.source_root, "**", simple_name)
            matches = glob.glob(pattern, recursive=True)

        if not matches:
            return ""

        # 读取文件，提取方法片段
        src_path = matches[0]
        try:
            with open(src_path, "r", encoding="utf-8") as f:
                source_lines = f.readlines()
        except Exception:
            return ""

        # 找到方法定义，提取上下文（方法名前后 30 行）
        method_name = node.method
        method_start = -1
        for i, line in enumerate(source_lines):
            # 匹配方法定义行（简单启发式）
            if method_name in line and ("(" in line) and not line.strip().startswith("//"):
                # 排除调用（检查是否有返回类型或修饰符）
                stripped = line.strip()
                if any(kw in stripped for kw in ["public", "private", "protected", "void", "static"]) \
                   or stripped.startswith(method_name):
                    method_start = i
                    break

        if method_start == -1:
            return ""

        # 从方法定义开始，找到方法结束（简单计数大括号）
        brace_count = 0
        method_end = method_start
        started = False
        for i in range(method_start, min(method_start + 60, len(source_lines))):
            line = source_lines[i]
            brace_count += line.count("{") - line.count("}")
            if "{" in line:
                started = True
            if started and brace_count <= 0:
                method_end = i
                break
            method_end = i

        # 返回方法源码（最多 40 行）
        snippet_lines = source_lines[method_start:method_end + 1]
        if len(snippet_lines) > 40:
            snippet_lines = snippet_lines[:40] + ["    // ... (省略)\n"]

        return "".join(snippet_lines).rstrip()

    def _parse_response(self, response: str, api_entry: APIEntry) -> HttpParameter:
        """
        解析 LLM 的回复，提取 HTTP 请求 JSON。

        支持以下格式（按优先级）：
          1. <json>...</json> 标签
          2. ```json ... ``` 代码块（兼容旧格式）
          3. 纯 JSON 文本

        <think>...</think> 部分会被忽略（仅用于推理过程）。
        """
        # 优先: 提取 <json>...</json> 标签内容
        json_match = re.search(r'<json>\s*(.*?)\s*</json>', response, re.DOTALL)
        if not json_match:
            # 兼容: 提取 ```json ... ``` 代码块
            json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', response, re.DOTALL)
        if json_match:
            json_str = json_match.group(1).strip()
        else:
            # 最后尝试: 去掉 <think>...</think> 后直接解析
            cleaned = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL).strip()
            json_str = cleaned

        try:
            obj = json.loads(json_str)
        except json.JSONDecodeError:
            # 如果解析失败，使用默认值
            return HttpParameter(
                scene="fuzz_fallback",
                method=api_entry.http_method,
                url=f"{self.base_url}{api_entry.http_path}",
            )

        # 提取字段
        method = obj.get("method", api_entry.http_method)
        url = obj.get("url", f"{self.base_url}{api_entry.http_path}")
        headers = obj.get("headers", {})
        body_val = obj.get("body")

        # body 可能是 dict 或 string
        if body_val is not None and not isinstance(body_val, str):
            body = json.dumps(body_val, ensure_ascii=False)
        else:
            body = body_val

        return HttpParameter(
            scene="fuzz",
            method=method,
            url=url,
            headers=headers if isinstance(headers, dict) else {},
            body=body,
        )
