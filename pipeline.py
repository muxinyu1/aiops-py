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
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from expected_path import APIEntry, ExpectedPath
from fuzzer import FuzzAttempt, Fuzzer
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
    REACHED = "reached"           # 成功到达 sink
    UNREACHABLE = "unreachable"   # 达到最大尝试次数仍未到达
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
            PathStatus.UNREACHABLE: "❌",
            PathStatus.ERROR: "⚠️",
        }
        icon = status_icon.get(self.status, "?")
        path_desc = self.expected_path.id if self.expected_path else "?"
        return f"{icon} {path_desc} — {self.status.value} ({self.num_attempts} attempts, {self.elapsed_seconds:.1f}s)"


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
            f"成功到达: {self.reached_count} ({self.reach_rate:.0%})",
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


# ═══════════════════════════════════════════════════════════════════════════════
# Pipeline 主控
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Pipeline:
    """
    Sink-Centric Fuzz 主控循环。

    对每条预期路径执行：
      fuzzer 生成参数 → executor 执行 → 检查 sink → 偏差反馈 → 重试

    Args:
        fuzzer: LLM Fuzz Agent
        execute_fn: 执行函数，发送请求并返回 trace
        path_differ: 偏差计算器
        max_attempts: 每条路径的最大尝试次数
        sink_checker: sink 到达检查函数 (默认方法级检查)
    """

    fuzzer: Fuzzer
    execute_fn: ExecuteFn
    path_differ: PathDiffer = field(default_factory=PathDiffer)
    max_attempts: int = 10
    sink_checker: Callable[[Trace, Sink], bool] = check_sink_reached

    def run_single_path(
        self,
        api_entry: APIEntry,
        sink: Sink,
        expected_path: ExpectedPath,
    ) -> PathResult:
        """
        对单条预期路径运行 fuzz 循环。

        循环步骤：
          1. Fuzzer 生成 HTTP 请求
          2. Executor 执行请求，获取 trace
          3. 检查是否到达 sink → 成功则退出
          4. 计算偏差（trace vs expected_path）
          5. 将偏差反馈给 fuzzer → 回到步骤 1
          6. 超过 max_attempts 次则标记为不可达
        """
        start_time = time.time()
        history: list[FuzzAttempt] = []
        result = PathResult(expected_path=expected_path, sink=sink)

        logger.info(f"开始 fuzz: {api_entry.id} → {sink.qualified_method}")

        for attempt_num in range(1, self.max_attempts + 1):
            try:
                # Step 1: Fuzzer 生成请求参数
                logger.info(f"  第 {attempt_num}/{self.max_attempts} 次尝试...")
                param = self.fuzzer.fuzz(api_entry, sink, expected_path, history)
                logger.info(f"    请求: {param.method} {param.url}")

                # Step 2: 执行请求，获取 trace
                trace = self.execute_fn(param)

                # Step 3: 检查是否到达 sink
                reached = self.sink_checker(trace, sink)

                if reached:
                    logger.info(f"  ✅ 第 {attempt_num} 次成功到达 sink!")
                    attempt = FuzzAttempt(request=param, reached_sink=True)
                    history.append(attempt)
                    result.status = PathStatus.REACHED
                    result.successful_request = param
                    result.attempts = history
                    result.elapsed_seconds = time.time() - start_time
                    return result

                # Step 4: 未到达 sink — 计算偏差
                divergence = self.path_differ.diff(trace, expected_path)
                logger.info(f"    偏差: {divergence.summary}")

                # Step 5: 记录本次尝试，反馈给下一轮
                attempt = FuzzAttempt(
                    request=param,
                    divergence=divergence,
                    reached_sink=False,
                )
                history.append(attempt)

            except Exception as e:
                logger.warning(f"    执行异常: {e}")
                # 记录失败尝试（无 divergence）
                if 'param' in locals():
                    history.append(FuzzAttempt(request=param, reached_sink=False))
                else:
                    # Fuzzer 本身出错（如 LLM 调用失败）
                    result.status = PathStatus.ERROR
                    result.error_message = str(e)
                    result.elapsed_seconds = time.time() - start_time
                    result.attempts = history
                    return result

        # 达到最大尝试次数
        logger.info(f"  ❌ 达到最大尝试次数 ({self.max_attempts})，标记为不可达")
        result.status = PathStatus.UNREACHABLE
        result.attempts = history
        result.elapsed_seconds = time.time() - start_time
        return result

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