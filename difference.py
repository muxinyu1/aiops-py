"""
difference.py — 两个 Trace 之间的偏差描述

Difference 表示两个 trace 在基本块执行流中第一次出现分歧的位置,
即: 在同一类、同一方法中, 一个执行了某行而另一个没有, 或者执行流到达了不同的分支。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DivergencePoint:
    """
    执行流分歧点 — 两个 trace 第一次出现不同的基本块位置.

    描述: 在同一个源文件的同一个方法中,
    trace_a 执行了某些行, trace_b 执行了不同的行,
    分歧从 diverge_line 开始。
    """
    # ── 位置 ─────────────────────────────────────────────────────
    class_name: str             # 完全限定类名, e.g. "com/example/service/UserService"
    src_file: str               # 源文件名, e.g. "UserService.java"
    diverge_line: int           # 分歧起始行号 (第一个不一致的行)

    # ── 分歧详情 ─────────────────────────────────────────────────
    only_in_a: list[int] = field(default_factory=list)
    # trace_a 执行但 trace_b 未执行的行号

    only_in_b: list[int] = field(default_factory=list)
    # trace_b 执行但 trace_a 未执行的行号

    # ── 上下文 ───────────────────────────────────────────────────
    common_prefix: list[int] = field(default_factory=list)
    # 分歧前双方共同执行的行号序列 (提供上下文)


@dataclass
class Difference:
    """
    两个 Trace 之间的偏差计算结果.

    核心语义: 基于基本块执行流 (JaCoCo 行级覆盖) 的差异,
    找到第一个分歧点, 以及所有分歧点的汇总。
    """

    # ── 是否存在差异 ─────────────────────────────────────────────
    has_divergence: bool = False
    # True 表示两个 trace 的执行流存在分歧

    # ── 第一个分歧点 (最重要) ────────────────────────────────────
    first_divergence: Optional[DivergencePoint] = None
    # 执行流中第一次出现不一致的位置

    # ── 所有分歧点 ───────────────────────────────────────────────
    all_divergences: list[DivergencePoint] = field(default_factory=list)
    # 所有类/方法中检测到的分歧点列表 (按执行顺序排列)

    # ── 类级别差异汇总 ───────────────────────────────────────────
    classes_only_in_a: list[str] = field(default_factory=list)
    # 仅在 trace_a 中有覆盖的类 (trace_b 完全未触达)

    classes_only_in_b: list[str] = field(default_factory=list)
    # 仅在 trace_b 中有覆盖的类 (trace_a 完全未触达)

    # ── 统计 ─────────────────────────────────────────────────────
    total_divergent_lines: int = 0
    # 总分歧行数 (only_in_a + only_in_b 的合集)

    @property
    def divergence_summary(self) -> str:
        """生成人类可读的分歧摘要."""
        if not self.has_divergence:
            return "No divergence: execution flows are identical."

        fp = self.first_divergence
        if fp is None:
            return "Divergence detected but no specific point identified."

        return (
            f"First divergence at {fp.src_file}:{fp.diverge_line} "
            f"(class: {fp.class_name}). "
            f"Lines only in A: {fp.only_in_a[:5]}{'...' if len(fp.only_in_a) > 5 else ''}, "
            f"only in B: {fp.only_in_b[:5]}{'...' if len(fp.only_in_b) > 5 else ''}."
        )