"""
pipeline.py — Sink-Centric Fuzz 主控循环

遍历每条预期路径，对每条路径运行 fuzz agent 循环：
  1. Fuzzer 生成请求参数
  2. Executor 执行请求，获取 trace
  3. 检查 trace 是否到达了目标 sink
  4. 如果未到达，计算偏差并反馈给 fuzzer
  5. 重复直到成功或达到最大尝试次数

参考 GONDAR 论文 Exploration Agent 的设计。
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from expected_path import APIEntry, ExpectedPath
from fuzzer import FuzzAttempt, FuzzConversationLog, Fuzzer
from llm import LLM
from parameter import HttpParameter
from path_differ import PathDiffer, PathDivergence
from sink import Sink
from trace import Trace, TraceNode

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# 结果数据模型
# ═══════════════════════════════════════════════════════════════════════════════

class PathStatus(str, Enum):
    """单条预期路径的 fuzz 结果状态."""
    REACHED = "reached"           # 攻击成功: 到达 sink + marker 出现
    REACHED_NO_MARKER = "reached_no_marker"  # 到达 sink 但 marker 未出现（注入失败）
    UNREACHABLE = "unreachable"   # 达到最大尝试次数仍未到达 sink
    ERROR = "error"               # 执行出错


@dataclass
class PathResult:
    """单条预期路径的 fuzz 结果."""
    expected_path: ExpectedPath
    sink: Sink
    status: PathStatus = PathStatus.UNREACHABLE
    attempts: list[FuzzAttempt] = field(default_factory=list)
    successful_request: Optional[HttpParameter] = None
    elapsed_seconds: float = 0.0
    error_message: str = ""

    @property
    def num_attempts(self) -> int:
        return len(self.attempts)

    @property
    def summary(self) -> str:
        status_icon = {
            PathStatus.REACHED: "✅",
            PathStatus.REACHED_NO_MARKER: "⚠️",
            PathStatus.UNREACHABLE: "❌",
            PathStatus.ERROR: "💥",
        }
        status_desc = {
            PathStatus.REACHED: "attacked",
            PathStatus.REACHED_NO_MARKER: "reached sink, marker not injected",
            PathStatus.UNREACHABLE: "unreachable",
            PathStatus.ERROR: "error",
        }
        icon = status_icon.get(self.status, "?")
        desc = status_desc.get(self.status, self.status.value)
        path_desc = self.expected_path.id if self.expected_path else "?"
        return f"{icon} {path_desc} — {desc} ({self.num_attempts} attempts, {self.elapsed_seconds:.1f}s)"


@dataclass
class PipelineResult:
    """整个 pipeline 的 fuzz 结果汇总."""
    path_results: list[PathResult] = field(default_factory=list)
    total_elapsed: float = 0.0

    @property
    def reached_count(self) -> int:
        return sum(1 for r in self.path_results if r.status == PathStatus.REACHED)

    @property
    def total_count(self) -> int:
        return len(self.path_results)

    @property
    def reach_rate(self) -> float:
        return self.reached_count / self.total_count if self.total_count > 0 else 0.0

    @property
    def summary(self) -> str:
        lines = [
            f"═══ Fuzz Pipeline 结果 ═══",
            f"总路径数: {self.total_count}",
            f"攻击成功: {self.reached_count} ({self.reach_rate:.0%})",
            f"总耗时: {self.total_elapsed:.1f}s",
            f"",
        ]
        for r in self.path_results:
            lines.append(f"  {r.summary}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# Sink 到达检查
# ═══════════════════════════════════════════════════════════════════════════════

def check_sink_reached(trace: Trace, sink: Sink) -> bool:
    """
    检查 trace 中是否到达了目标 sink 方法。

    判断逻辑：trace 的 nodes 中存在一个节点，其 class_namespace 和 function
    与 sink 的 class_name 和 method 匹配。

    注意：这里检查的是"方法被执行"，而非精确到行号。
    因为 trace 粒度是方法级（span），行号信息需要 JaCoCo 覆盖率辅助。
    """
    if not trace or not trace.nodes:
        return False

    # 标准化 sink class name (确保用 . 分隔)
    sink_class = sink.class_name.replace("/", ".")

    for node in trace.nodes:
        node_class = node.class_namespace.replace("/", ".")
        if node_class == sink_class and node.function == sink.method:
            return True

    return False


def check_sink_reached_with_line(trace: Trace, sink: Sink) -> bool:
    """
    更精确的 sink 检查：不仅要求方法匹配，还要求行号覆盖。

    需要 JaCoCo 行级覆盖数据（node.executed_lines 非空）。
    如果没有行级数据，退化为方法级检查。
    """
    if not trace or not trace.nodes:
        return False

    sink_class = sink.class_name.replace("/", ".")

    for node in trace.nodes:
        node_class = node.class_namespace.replace("/", ".")
        if node_class == sink_class and node.function == sink.method:
            # 如果有行级数据且 sink 有行号，做精确检查
            if sink.line_number > 0 and node.executed_lines:
                if sink.line_number in node.executed_lines:
                    return True
            else:
                # 退化为方法级匹配
                return True

    return False


# ═══════════════════════════════════════════════════════════════════════════════
# 执行器接口（抽象）
# ═══════════════════════════════════════════════════════════════════════════════

# ExecuteFn 类型: 接收 HttpParameter, 返回 Trace
ExecuteFn = Callable[[HttpParameter], Trace]

# CheckLogFn 类型: 检查容器日志第 N 行之后是否出现攻击标记 (marker, skip_lines) -> bool
CheckLogFn = Callable[[str, int], bool]

# GetLogLineCountFn 类型: 获取容器当前日志总行数 () -> int
GetLogLineCountFn = Callable[[], int]


# ═══════════════════════════════════════════════════════════════════════════════
# Pipeline 主控
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Pipeline:
    """
    Log Injection Fuzz 主控循环。

    对每条预期路径执行：
      fuzzer 生成参数 → executor 执行 → 检查 sink → 检查日志 → 偏差反馈 → 重试

    攻击成功判定 = trace 到达 sink + 容器日志出现攻击标记

    Args:
        fuzzer: LLM Fuzz Agent
        execute_fn: 执行函数，发送请求并返回 trace
        check_log_fn: 日志检查函数，(marker, skip_lines) -> bool
        get_log_line_count_fn: 获取容器当前日志总行数
        path_differ: 偏差计算器
        max_attempts: 每条路径的最大尝试次数
        sink_checker: sink 到达检查函数 (默认方法级检查)
    """

    fuzzer: Fuzzer
    execute_fn: ExecuteFn
    check_log_fn: CheckLogFn  # (marker, skip_lines) -> bool
    get_log_line_count_fn: GetLogLineCountFn  # () -> int
    path_differ: PathDiffer = field(default_factory=PathDiffer)
    max_attempts: int = 10
    sink_checker: Callable[[Trace, Sink], bool] = check_sink_reached
    log_dir: str = "logs/fuzz"  # 对话日志保存目录

    def run_single_path(
        self,
        api_entry: APIEntry,
        sink: Sink,
        expected_path: ExpectedPath,
    ) -> PathResult:
        """
        对单条预期路径运行 fuzz 循环。

        攻击成功 = trace 到达 sink method + 容器日志中出现攻击标记

        循环步骤：
          1. Fuzzer 生成 HTTP 请求（含攻击标记 payload）
          2. Executor 执行请求，获取 trace
          3. 检查 trace 是否到达 sink
          4. 检查容器日志是否出现攻击标记
          5. 两者都满足 → 攻击成功
          6. 否则计算偏差，反馈给 fuzzer → 回到步骤 1
        """
        start_time = time.time()
        history: list[FuzzAttempt] = []
        result = PathResult(expected_path=expected_path, sink=sink)
        attack_marker = self.fuzzer.attack_marker

        # 开始对话日志
        self.fuzzer.start_conversation_log(api_entry, sink)

        logger.info(f"开始攻击: {api_entry.id} → {sink.qualified_method}")
        logger.info(f"  攻击标记: \"{attack_marker}\"")

        for attempt_num in range(1, self.max_attempts + 1):
            try:
                # Step 1: Fuzzer 生成请求参数
                logger.info(f"  第 {attempt_num}/{self.max_attempts} 次尝试...")
                param = self.fuzzer.fuzz(api_entry, sink, expected_path, history)
                logger.info(f"    请求: {param.method} {param.url}")

                # 记录请求前的日志行数（用于只检查新增日志）
                log_line_count_before = self.get_log_line_count_fn()

                # Step 2: 执行请求，获取 trace
                trace = self.execute_fn(param)

                # Step 3: 检查是否到达 sink
                reached = self.sink_checker(trace, sink)

                # Step 4: 检查容器日志是否出现攻击标记（只看请求之后的新增行）
                marker_found = self.check_log_fn(attack_marker, log_line_count_before)

                # 更新对话日志中最后一条记录的执行结果
                self.fuzzer.update_last_call(param, reached, marker_found)

                # Step 5: 判定攻击是否成功
                if reached and marker_found:
                    logger.info(f"  🎯 第 {attempt_num} 次攻击成功! sink 到达 + 日志注入成功")
                    attempt = FuzzAttempt(
                        request=param, reached_sink=True, marker_found=True,
                    )
                    history.append(attempt)
                    result.status = PathStatus.REACHED
                    result.successful_request = param
                    result.attempts = history
                    result.elapsed_seconds = time.time() - start_time
                    self._save_conversation_log(PathStatus.REACHED)
                    return result

                # 部分成功的提示
                if reached and not marker_found:
                    logger.info(f"    ⚠️ 到达 sink 但日志中未出现攻击标记")
                elif not reached:
                    logger.info(f"    ❌ 未到达 sink")

                # Step 6: 计算偏差，记录反馈
                divergence = self.path_differ.diff(trace, expected_path)
                if not reached:
                    logger.info(f"    偏差: {divergence.summary}")

                attempt = FuzzAttempt(
                    request=param,
                    divergence=divergence,
                    reached_sink=reached,
                    marker_found=marker_found,
                )
                history.append(attempt)

            except Exception as e:
                logger.warning(f"    执行异常: {e}")
                if 'param' in locals():
                    history.append(FuzzAttempt(request=param, reached_sink=False))
                else:
                    result.status = PathStatus.ERROR
                    result.error_message = str(e)
                    result.elapsed_seconds = time.time() - start_time
                    result.attempts = history
                    self._save_conversation_log(PathStatus.ERROR)
                    return result

        # 达到最大尝试次数
        logger.info(f"  ❌ 达到最大尝试次数 ({self.max_attempts})，攻击失败")
        # 区分: 是否曾经到达过 sink
        ever_reached = any(a.reached_sink for a in history)
        if ever_reached:
            result.status = PathStatus.REACHED_NO_MARKER
        else:
            result.status = PathStatus.UNREACHABLE
        result.attempts = history
        result.elapsed_seconds = time.time() - start_time
        self._save_conversation_log(result.status)
        return result

    def _save_conversation_log(self, status: PathStatus) -> None:
        """保存当前路径的 LLM 对话日志为 JSON 文件."""
        conv_log = self.fuzzer.finalize_conversation_log(status.value)
        if conv_log is None:
            return

        os.makedirs(self.log_dir, exist_ok=True)

        # 文件名: {api}_{sink}_{timestamp}.json
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        api_safe = conv_log.api_entry_id.replace("/", "_").replace(" ", "_").strip("_")
        sink_safe = conv_log.sink_id.split(".")[-1] if "." in conv_log.sink_id else conv_log.sink_id
        filename = f"{api_safe}__{sink_safe}__{ts}.json"
        filepath = os.path.join(self.log_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(conv_log.to_dict(), f, ensure_ascii=False, indent=2)

        logger.info(f"  📝 对话日志已保存: {filepath}")

    def run(
        self,
        targets: list[tuple[APIEntry, Sink, ExpectedPath]],
    ) -> PipelineResult:
        """
        对多条预期路径批量执行 fuzz。

        Args:
            targets: 列表，每个元素是 (API入口, 目标Sink, 预期路径) 三元组

        Returns:
            PipelineResult: 所有路径的 fuzz 结果汇总
        """
        start_time = time.time()
        pipeline_result = PipelineResult()

        logger.info(f"═══ Fuzz Pipeline 启动: {len(targets)} 条路径 ═══")

        for i, (api_entry, sink, expected_path) in enumerate(targets, 1):
            logger.info(f"\n[{i}/{len(targets)}] {api_entry.id} → {sink.qualified_method}")
            path_result = self.run_single_path(api_entry, sink, expected_path)
            pipeline_result.path_results.append(path_result)

        pipeline_result.total_elapsed = time.time() - start_time
        logger.info(f"\n{pipeline_result.summary}")
        return pipeline_result
        pass