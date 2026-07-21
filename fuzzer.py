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
你是一个安全测试专家，正在对一个 Java 微服务进行 sink-centric fuzzing。

## 目标

你需要构造 HTTP 请求，使得请求的执行路径能够到达以下目标日志打印点（sink）：

**目标 Sink**: `{sink_class}.{sink_method}` (第 {sink_line} 行)
- 日志级别: {sink_type}
- 日志内容: `{sink_message}`

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

请以 JSON 格式输出你要发送的 HTTP 请求，格式如下：
```json
{{
  "method": "GET 或 POST 等",
  "url": "完整 URL（包括路径参数的具体值）",
  "headers": {{"Header-Name": "value"}},
  "body": null 或 JSON 字符串
}}
```

## 重要提示

1. URL 中的路径参数（如 `{{id}}`）必须替换为具体值
2. POST/PUT 请求需要提供 JSON body
3. 目标是让代码执行到达 sink 点，触发目标日志语句
4. 分析预期路径中每个方法的参数要求，构造能满足路径约束的输入
5. 只输出一个 JSON 代码块，不要输出多余内容
"""

_USER_PROMPT_FIRST = """\
请构造第一个请求来尝试到达目标 sink。

分析预期路径，推断：
1. API 需要什么参数才能触发预期的调用链
2. 参数的值应该是什么才能让执行走到 sink 分支
"""

_USER_PROMPT_WITH_FEEDBACK = """\
上一次请求未能到达目标 sink。以下是偏差分析：

## 第 {attempt_num} 次尝试结果

**发送的请求**: {prev_request}

**执行偏差**:
{divergence_info}

## 历史失败请求
{history_summary}

## 要求

根据以上偏差信息，调整你的请求参数。思考：
1. 执行为什么在偏差点停下了？是参数不满足某个条件吗？
2. 需要什么样的参数值才能让执行继续走到下一个节点？
3. 不要重复之前失败的请求

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


@dataclass
class Fuzzer:
    """
    LLM 驱动的 Sink-Centric Fuzz Agent。

    对于一条预期路径 (API entry → sink)，通过多轮 LLM 对话
    迭代生成请求参数，利用偏差反馈引导执行路径逼近 sink。
    """

    llm: LLM
    base_url: str = "http://localhost:8080"  # 目标服务地址

    def build_system_prompt(
        self,
        api_entry: APIEntry,
        sink: Sink,
        expected_path: ExpectedPath,
    ) -> str:
        """构造 system prompt，描述目标 sink、API 和预期路径."""

        # 参数信息
        param_info = self._build_param_info(api_entry)

        # 预期路径描述
        path_nodes = expected_path.nodes if expected_path.nodes else []
        expected_path_str = " → ".join(
            f"`{node.class_name.split('.')[-1]}.{node.method}`"
            for node in path_nodes
        ) if path_nodes else "(无详细路径信息)"

        # 路径参数替换提示
        http_path = api_entry.http_path

        return _SYSTEM_PROMPT_TEMPLATE.format(
            sink_class=sink.class_name.split('.')[-1],
            sink_method=sink.method,
            sink_line=sink.line_number,
            sink_type=sink.sink_type.value if hasattr(sink.sink_type, 'value') else str(sink.sink_type),
            sink_message=sink.log_message or "(未知)",
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
        """构造 user prompt，包含偏差反馈和历史失败请求."""

        if not history:
            return _USER_PROMPT_FIRST

        last = history[-1]
        attempt_num = len(history)

        # 上一次请求描述
        prev_request = self._format_request(last.request)

        # 偏差描述
        divergence_info = self._format_divergence(last.divergence)

        # 历史摘要（最多展示最近 5 次）
        recent = history[-5:]
        history_lines = []
        for i, attempt in enumerate(recent, 1):
            req_desc = self._format_request(attempt.request)
            div_desc = attempt.divergence.summary if attempt.divergence else "无偏差数据"
            history_lines.append(f"  尝试 {i}: {req_desc} → {div_desc}")
        history_summary = "\n".join(history_lines)

        return _USER_PROMPT_WITH_FEEDBACK.format(
            attempt_num=attempt_num,
            prev_request=prev_request,
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
        """将 HttpParameter 转为 JSON 字符串（模拟 LLM 之前的输出）."""
        obj = {
            "method": param.method,
            "url": param.url,
            "headers": param.headers,
            "body": json.loads(param.body) if param.body else None,
        }
        return f"```json\n{json.dumps(obj, indent=2, ensure_ascii=False)}\n```"

    def _format_divergence(self, div: Optional[PathDivergence]) -> str:
        """将 PathDivergence 格式化为可读的偏差描述."""
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

        return "\n".join(lines)

    def _parse_response(self, response: str, api_entry: APIEntry) -> HttpParameter:
        """
        解析 LLM 的回复，提取 JSON 格式的 HTTP 请求。

        支持从 markdown 代码块或纯 JSON 中提取。
        """
        # 尝试提取 ```json ... ``` 代码块
        json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', response, re.DOTALL)
        if json_match:
            json_str = json_match.group(1).strip()
        else:
            # 尝试直接解析整个响应
            json_str = response.strip()

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
