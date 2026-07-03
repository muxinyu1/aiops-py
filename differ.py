"""
differ.py — 偏差计算器

基于基本块执行流 (JaCoCo 行级覆盖) 计算两个 Trace 之间的差异.
核心逻辑: 按类逐一对比 covered_lines, 找到第一个出现分歧的行号。
"""

from __future__ import annotations

from dataclasses import dataclass

from trace import Trace, CoverageData, LineCoverage
from difference import Difference, DivergencePoint


@dataclass
class Differ:
    """
    计算两个 Trace 之间的基本块执行流偏差.

    算法:
      1. 提取两个 trace 的行级覆盖数据 (CoverageData.class_coverages)
      2. 按类名对齐, 对比每个类的 covered_lines 集合
      3. 对于有差异的类, 找到第一行分歧 (最小的不一致行号)
      4. 所有分歧点按行号排序, 第一个即为 first_divergence
    """

    def diff(self, trace_a: Trace, trace_b: Trace) -> Difference:
        """
        计算 trace_a 与 trace_b 在基本块执行流上的偏差.

        Args:
            trace_a: 基准 trace (通常是正常请求)
            trace_b: 目标 trace (通常是异常请求)

        Returns:
            Difference 描述两者第一次分歧的位置及详情
        """
        cov_a = trace_a.coverage
        cov_b = trace_b.coverage

        # 如果任一 trace 没有覆盖数据, 无法比较
        if cov_a is None or cov_b is None:
            return Difference(has_divergence=False)

        # 按类名索引覆盖数据
        map_a = self._build_class_map(cov_a)
        map_b = self._build_class_map(cov_b)

        all_classes = sorted(set(map_a.keys()) | set(map_b.keys()))

        divergences: list[DivergencePoint] = []
        classes_only_in_a: list[str] = []
        classes_only_in_b: list[str] = []
        total_divergent_lines = 0

        for class_name in all_classes:
            lc_a = map_a.get(class_name)
            lc_b = map_b.get(class_name)

            if lc_a is None:
                # 该类仅在 trace_b 中有覆盖
                classes_only_in_b.append(class_name)
                if lc_b:
                    total_divergent_lines += len(lc_b.covered_lines)
                continue

            if lc_b is None:
                # 该类仅在 trace_a 中有覆盖
                classes_only_in_a.append(class_name)
                total_divergent_lines += len(lc_a.covered_lines)
                continue

            # 两边都有覆盖, 比较执行的行集合
            lines_a = set(lc_a.covered_lines)
            lines_b = set(lc_b.covered_lines)

            only_in_a = sorted(lines_a - lines_b)
            only_in_b = sorted(lines_b - lines_a)

            if not only_in_a and not only_in_b:
                # 完全一致, 无分歧
                continue

            # 找到第一行分歧
            all_diff_lines = sorted(only_in_a + only_in_b)
            diverge_line = all_diff_lines[0]

            # 计算共同前缀 (分歧前双方都执行的行, 按顺序)
            common = sorted(lines_a & lines_b)
            common_prefix = [l for l in common if l < diverge_line]

            dp = DivergencePoint(
                class_name=class_name,
                src_file=lc_a.src_file,
                diverge_line=diverge_line,
                only_in_a=only_in_a,
                only_in_b=only_in_b,
                common_prefix=common_prefix,
            )
            divergences.append(dp)
            total_divergent_lines += len(only_in_a) + len(only_in_b)

        # 按分歧行号排序, 取第一个
        divergences.sort(key=lambda d: d.diverge_line)

        has_divergence = bool(divergences) or bool(classes_only_in_a) or bool(classes_only_in_b)

        # 如果没有具体的行内分歧, 但有类级分歧, 构造一个类级分歧点
        first_divergence = divergences[0] if divergences else None
        if first_divergence is None and has_divergence:
            # 类级分歧: 找第一个只在某一方出现的类
            if classes_only_in_a:
                cls = classes_only_in_a[0]
                lc = map_a[cls]
                first_divergence = DivergencePoint(
                    class_name=cls,
                    src_file=lc.src_file,
                    diverge_line=min(lc.covered_lines) if lc.covered_lines else 0,
                    only_in_a=sorted(lc.covered_lines),
                    only_in_b=[],
                )
            elif classes_only_in_b:
                cls = classes_only_in_b[0]
                lc = map_b[cls]
                first_divergence = DivergencePoint(
                    class_name=cls,
                    src_file=lc.src_file,
                    diverge_line=min(lc.covered_lines) if lc.covered_lines else 0,
                    only_in_a=[],
                    only_in_b=sorted(lc.covered_lines),
                )

        return Difference(
            has_divergence=has_divergence,
            first_divergence=first_divergence,
            all_divergences=divergences,
            classes_only_in_a=classes_only_in_a,
            classes_only_in_b=classes_only_in_b,
            total_divergent_lines=total_divergent_lines,
        )

    @staticmethod
    def _build_class_map(cov: CoverageData) -> dict[str, LineCoverage]:
        """将 CoverageData 的 class_coverages 按 class_name 索引."""
        return {lc.class_name: lc for lc in cov.class_coverages}