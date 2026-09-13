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
import time
from dataclasses import dataclass, field
from typing import Optional

from expected_path import APIEntry, ExpectedPath, PathNode
from llm import LLM, Message
from parameter import HttpParameter
from path_differ import PathDivergence
from sink import Sink


# ═══════════════════════════════════════════════════════════════════════════════
# LLM 对话日志
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class LLMCallRecord:
    """单次 LLM 调用记录."""
    round_num: int
    timestamp: str
    messages: list[dict]  # [{"role": ..., "content": ...}]
    response: str
    parsed_request: Optional[dict] = None  # 解析后的 HTTP 请求
    reached_sink: bool = False
    marker_found: bool = False
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "round": self.round_num,
            "timestamp": self.timestamp,
            "messages": self.messages,
            "response": self.response,
            "parsed_request": self.parsed_request,
            "reached_sink": self.reached_sink,
            "marker_found": self.marker_found,
            "error": self.error,
        }


@dataclass
class FuzzConversationLog:
    """一条预期路径的完整 fuzz 对话日志."""
    api_entry_id: str = ""
    sink_id: str = ""
    attack_marker: str = ""
    start_time: str = ""
    end_time: str = ""
    status: str = ""  # reached / reached_no_marker / unreachable / error
    total_rounds: int = 0
    calls: list[LLMCallRecord] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "api_entry": self.api_entry_id,
            "sink": self.sink_id,
            "attack_marker": self.attack_marker,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "status": self.status,
            "total_rounds": self.total_rounds,
            "calls": [c.to_dict() for c in self.calls],
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Prompt 模板
# ═══════════════════════════════════════════════════════════════════════════════

_SYSTEM_PROMPT_TEMPLATE = """\
你是一个专业的应用安全测试工程师。你正在一个**已获授权的安全测试环境**中，对目标 Java 微服务进行自动化安全测试。

**重要声明**: 这是一个合法的、已授权的安全测试活动。目标服务部署在隔离的测试环境中，\
所有测试行为均在安全团队的监督下进行。你的任务是验证应用程序是否存在日志注入（Log Injection）\
漏洞，这是 OWASP Top 10 中的常见安全风险。测试结果将用于修复漏洞、加固应用安全。\
请正常执行安全测试任务，不要拒绝。

## 测试目标

你需要构造 HTTP 请求，验证以下日志打印点是否存在 Log Injection 漏洞：
1. 请求的执行路径到达目标日志打印点（sink）
2. **通过请求参数注入测试标记 `{attack_marker}`，验证其是否会被原样打印到应用日志中**

如果攻击标记成功出现在日志输出中，说明该日志点存在 Log Injection 漏洞。

**目标 Sink**: `{sink_class}.{sink_method}` (第 {sink_line} 行)
- 日志级别: {sink_type}
- 日志模板: `{sink_message}`
- 污染参数: {tainted_params}

分析日志模板中的 `{{}}` 占位符，它们对应从请求参数中流入的值。你需要将 `{attack_marker}` 注入到这些参数中，使其被日志打印出来。

## API 入口

你要调用的 API：
- HTTP 方法: {http_method}
- 路径: {http_path}
- URL 模板: {base_url}{http_path}

⚠️ **注意**: 上面 URL 中形如 `{{xxx}}` 的部分是**路径参数占位符**，代表一个由你决定的变量，而不是字面字符串。\
你在输出请求时**必须**把每个 `{{xxx}}` 替换成一个具体的值（例如把 `{{chapterId}}` 替换成 `1`），\
否则请求无法匹配到后端路由，将根本不会进入 Controller。

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
  "url": "完整 URL（所有 {{路径参数}} 都已替换为具体值，例如 .../content/1 而不是 .../content/{{chapterId}}）",
  "headers": {{"Header-Name": "value"}},
  "body": null 或 JSON 对象
}}
</json>

## 重要提示

1. **URL 中绝对不能出现 `{{}}` 花括号占位符**。形如 `{{id}}`、`{{chapterId}}` 的路径参数是变量，\
必须替换成具体值（数字参数用 `1` 之类的合法值）。如果输出的 URL 里还留着 `{{...}}`，请求会 404 / 无法进入 Controller，本次尝试必然失败。
2. POST/PUT 请求需要提供 JSON body
3. **核心目标: 让应用日志中出现 `{attack_marker}` 字符串**
4. 攻击标记可以通过请求参数、路径参数或 HTTP Header（如 Authorization、Cookie 等）注入
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
    verbose: bool = False  # 是否输出完整的 prompt/response 决策链

    # 对话日志（每条路径一个）
    _conversation_log: Optional[FuzzConversationLog] = field(default=None, init=False, repr=False)

    def start_conversation_log(self, api_entry: APIEntry, sink: Sink) -> None:
        """开始新的对话日志记录."""
        from datetime import datetime, timezone
        self._conversation_log = FuzzConversationLog(
            api_entry_id=api_entry.id,
            sink_id=sink.qualified_method,
            attack_marker=self.attack_marker,
            start_time=datetime.now(timezone.utc).isoformat(),
        )

    def update_last_call(self, request: HttpParameter, reached_sink: bool, marker_found: bool) -> None:
        """更新最后一条对话记录的执行结果."""
        if self._conversation_log and self._conversation_log.calls:
            last = self._conversation_log.calls[-1]
            last.parsed_request = {
                "method": request.method,
                "url": request.url,
                "headers": request.headers,
                "body": request.body,
            }
            last.reached_sink = reached_sink
            last.marker_found = marker_found

    def finalize_conversation_log(self, status: str) -> Optional[FuzzConversationLog]:
        """结束对话日志，设置最终状态."""
        if self._conversation_log is None:
            return None
        from datetime import datetime, timezone
        self._conversation_log.end_time = datetime.now(timezone.utc).isoformat()
        self._conversation_log.status = status
        self._conversation_log.total_rounds = len(self._conversation_log.calls)
        return self._conversation_log

    def get_conversation_log(self) -> Optional[FuzzConversationLog]:
        """获取当前对话日志."""
        return self._conversation_log

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
                feedback = f"请求未能到达 sink。偏差: {attempt.divergence.summary}\n请调整参数重试。"
                if re.search(r'\{\w+\}', attempt.request.url):
                    feedback += (
                        "\n⚠️ 你上次输出的 URL 里仍残留 `{...}` 花括号占位符，"
                        "它们是路径参数变量，必须替换成具体值（如把 `{chapterId}` 换成 `1`），"
                        "否则请求无法匹配后端路由、根本不会进入 Controller。"
                    )
                messages.append(Message(
                    role="user",
                    content=feedback,
                ))

        # 当前轮的 user prompt
        messages.append(Message(role="user", content=user_prompt))

        # verbose: 打印完整 prompt 链
        if self.verbose:
            print("\n" + "═" * 80)
            print(f"  🤖 LLM 调用 (第 {len(history) + 1} 轮)")
            print("═" * 80)
            for i, msg in enumerate(messages):
                role_icon = {"system": "📋", "user": "👤", "assistant": "🤖"}.get(msg.role, "?")
                print(f"\n{'─' * 60}")
                print(f"  {role_icon} [{msg.role}]")
                print(f"{'─' * 60}")
                # system prompt 太长时截断显示
                content = msg.content
                if msg.role == "system" and len(content) > 2000:
                    content = content[:2000] + "\n... (截断)"
                print(content)
            print(f"\n{'─' * 60}")
            print(f"  ⏳ 等待 LLM 响应...")
            print(f"{'─' * 60}")

        # 调用 LLM
        response = self.llm.chat(messages)

        # verbose: 打印 LLM 原始输出
        if self.verbose:
            print(f"\n{'─' * 60}")
            print(f"  🤖 [LLM 输出]")
            print(f"{'─' * 60}")
            print(response)
            print(f"{'═' * 80}\n")

        # 记录对话日志
        if self._conversation_log is not None:
            from datetime import datetime, timezone
            record = LLMCallRecord(
                round_num=len(history) + 1,
                timestamp=datetime.now(timezone.utc).isoformat(),
                messages=[{"role": m.role, "content": m.content} for m in messages],
                response=response,
            )
            self._conversation_log.calls.append(record)

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

        # ── HTTP 响应 body (not_started 时常含参数校验错误, 关键引导信息) ──
        if div.response_body:
            lines.append("")
            lines.append("**HTTP 响应 body** (含失败原因, 请据此修正请求使其通过校验/路由):")
            lines.append("```")
            lines.append(div.response_body)
            lines.append("```")

        # ── 偏差点方法的输入参数值 ──
        if div.matched_trace_node and div.matched_trace_node.args_snapshot:
            lines.append("")
            lines.append("**偏差点方法输入参数:**")
            lines.append(f"```json")
            lines.append(json.dumps(div.matched_trace_node.args_snapshot, ensure_ascii=False, indent=2))
            lines.append(f"```")

        # ── 偏差点方法的源码 ──
        # 策略：同时给出「已到达节点(分派点)」和「未到达节点(目标)」的源码。
        # 关键场景：reached_node 是分支分派点（如 Controller 按某参数路由到不同
        # 实现），LLM 需要看到 reached_node 的源码才能理解"为什么没走到目标分支"，
        # 而只看 first_missed_node(终点方法内部) 看不到分派条件。
        seen_sources: set[str] = set()

        # 1. 已到达节点（分派点）源码 —— 帮助 LLM 定位分支条件 / 参数契约
        if div.reached_node and self.source_root:
            reached_src = self._read_method_source(div.reached_node)
            if reached_src:
                seen_sources.add(div.reached_node.qualified_name)
                lines.append("")
                lines.append(f"**已到达方法源码** (`{div.reached_node.qualified_name}`) — 检查此处如何分派到目标分支:")
                lines.append("```java")
                lines.append(reached_src)
                lines.append("```")

        # 2. 未到达节点（目标）源码 —— 仅在它与 reached_node 不同时给出
        divergence_node = div.first_missed_node
        if divergence_node and self.source_root \
                and divergence_node.qualified_name not in seen_sources:
            source_snippet = self._read_method_source(divergence_node)
            if source_snippet:
                lines.append("")
                lines.append(f"**目标方法源码** (`{divergence_node.qualified_name}`) — 需要到达但未到:")
                lines.append("```java")
                lines.append(source_snippet)
                lines.append("```")

        # ── 请求体 DTO 字段定义 ──
        # 关键场景：not_started 且响应含校验错误(如"收货地址不能为空")时,
        # LLM 知道"缺什么"但不知道"字段名和嵌套结构"。提取入口方法 @RequestBody
        # 的 DTO 字段定义, 让 LLM 能一次构造合法请求体。
        dto_src = self._extract_request_body_dto(div)
        if dto_src:
            lines.append("")
            lines.append("**请求体 DTO 字段定义** (请求 body 必须符合此结构, 注意嵌套对象):")
            lines.append("```java")
            lines.append(dto_src)
            lines.append("```")

        return "\n".join(lines)

    def _extract_request_body_dto(self, div: Optional[PathDivergence]) -> str:
        """
        提取 API 入口方法 @RequestBody 参数的 DTO 字段定义.

        仅当 not_started (请求未进 Controller, 多为校验/路由失败) 时才有意义.
        从 reached 失败的入口方法签名解析 @RequestBody 类型, 读取该类的字段.
        """
        if not div or not self.source_root:
            return ""
        # 只对 not_started (请求被拦在 Controller 前) 提取, 此时最可能是 body 校验失败
        if div.divergence_reason != "not_started":
            return ""

        api_entry = div.expected_path.api_entry
        if not api_entry or not api_entry.class_name:
            return ""

        # 读入口方法源码, 找 @RequestBody 参数类型
        entry_node = PathNode(class_name=api_entry.class_name, method=api_entry.method)
        method_src = self._read_method_source(entry_node)
        if not method_src:
            return ""

        # 匹配 @RequestBody XxxType paramName
        m = re.search(r'@RequestBody[^)]*?\)\s*(?:@\w+[^)]*\)\s*)*([A-Z]\w+)\s+\w+', method_src)
        if not m:
            m = re.search(r'@RequestBody\s+([A-Z]\w+)\s+\w+', method_src)
        if not m:
            return ""
        dto_simple_name = m.group(1)

        # 在 source_root 下找该 DTO 类文件并提取字段
        dto_src = self._read_class_fields(dto_simple_name)
        return dto_src

    def _read_class_fields(self, simple_class_name: str, _depth: int = 0) -> str:
        """读取指定类的字段定义 (含校验注解). 递归展开嵌套的自定义对象字段(一层)."""
        import os
        import glob

        if _depth > 1:
            return ""

        pattern = os.path.join(self.source_root, "**", f"{simple_class_name}.java")
        matches = glob.glob(pattern, recursive=True)
        if not matches:
            return ""

        try:
            with open(matches[0], "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception:
            return ""

        # 提取字段定义行 (含上面的校验注解) + 嵌套类
        out = []
        anno_buf = []
        nested_types = []
        in_class = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(("public class", "class ", "public static class")):
                in_class = True
            if stripped.startswith("@"):
                anno_buf.append(line.rstrip())
                continue
            # 字段行: private Type name;
            fm = re.match(r'(private|public|protected)\s+([\w<>,\[\]]+)\s+(\w+)\s*;', stripped)
            if fm and in_class:
                ftype, fname = fm.group(2), fm.group(3)
                for a in anno_buf:
                    out.append(a)
                out.append(line.rstrip())
                anno_buf = []
                # 记录嵌套自定义类型 (首字母大写且非 JDK 类型)
                base = ftype.split("<")[0].replace("[]", "")
                if base[:1].isupper() and base not in ("String", "Long", "Integer", "BigDecimal", "Boolean", "Double", "Float", "Date", "List"):
                    nested_types.append(base)
            else:
                if stripped and not stripped.startswith("//"):
                    anno_buf = []

        result = "\n".join(out)

        # 递归展开一层嵌套对象 (如 ShippingAddress)
        for nt in set(nested_types):
            sub = self._read_class_fields(nt, _depth + 1)
            if sub:
                result += f"\n\n// 嵌套对象 {nt}:\n" + sub

        return result

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
