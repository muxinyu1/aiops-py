"""
analyze_reachability.py — 按"HTTP可达性 → 黑盒可fuzz性"两层重组分类结果

第一层: HTTP 请求可达性
第二层: HTTP 可达中, 黑盒可 fuzz / 不可 fuzz(按原因)

用法: uv run python analyze_reachability.py
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict

INPUT = "data/sink_llm_classify.json"

# ── 类别 → 二级归类 ──
# HTTP 不可达
UNREACHABLE = {"non_http_entry"}
# HTTP 可达 + 黑盒可 fuzz (对照组)
BLACKBOX_FUZZABLE = {"single_param_or_empty", "other"}
# HTTP 可达 + 黑盒不可 fuzz → 按原因
NOT_FUZZABLE_REASONS = {
    "db_state": "依赖DB状态: 不知道需要什么种子数据/记录",
    "exception_path": "依赖内部异常: 需先在服务内部制造特定异常",
    "remote_service": "依赖下游服务: Feign/RPC/ES/OSS 返回值不可控",
    "cache_state": "依赖缓存状态: Redis/本地缓存 key 与内容不可知",
    "internal_state": "依赖内部状态: 配置开关/环境变量/队列/静态常量",
    "printed_value_not_request": "可达但内容不可控: {} 打印值来自DB/内部而非请求",
}
UNKNOWN = {"no_source"}

# 每类源码侧可行方案 (我们的优势: 有源码 + 可控部署环境)
SOLUTIONS = {
    "db_state": "读源码找出查询条件 → 直接向 MySQL 预置种子数据/篡改记录字段 (Docker 内 psql/mysql 或 API 预创建)",
    "exception_path": "读源码找 catch 的异常类型 → 构造触发该异常的非法输入 (如畸形参数/超长值/类型不匹配), 或 mock 内部依赖抛异常",
    "remote_service": "读源码找下游地址 → 用 Docker 网络把下游域名指向假服务 (mock Feign provider/ES/OSS), 控制返回值",
    "cache_state": "读源码找缓存 key 结构 → 直接向 Redis 写入目标 key (redis-cli), 预置缓存状态",
    "internal_state": "读源码找配置项 → 修改 application.yml/环境变量后重启容器, 打开开关",
    "printed_value_not_request": "读源码追打印值来源 → 若来自DB则预置数据, 若来自内部计算则该 sink 仅作可达性验证, 不作内容注入",
}


def main():
    with open(INPUT) as f:
        data = json.load(f)

    total = len(data)
    cats = Counter(r["category"] for r in data)

    # ── 第一层: HTTP 可达性 ──
    n_unreachable = sum(cats[c] for c in UNREACHABLE)
    n_unknown = sum(cats[c] for c in UNKNOWN)
    n_reachable = total - n_unreachable - n_unknown

    print("=" * 72)
    print("第一层: HTTP 请求可达性")
    print("=" * 72)
    print(f"  HTTP 可达:      {n_reachable:>5} ({n_reachable/total*100:.1f}%)")
    print(f"  HTTP 不可达:    {n_unreachable:>5} ({n_unreachable/total*100:.1f}%)  [non_http_entry]")
    print(f"  无源码无法判定: {n_unknown:>5} ({n_unknown/total*100:.1f}%)")
    print(f"  总计:           {total:>5}")

    # ── 第二层: HTTP 可达中, 黑盒可 fuzz 性 ──
    print()
    print("=" * 72)
    print(f"第二层: HTTP 可达的 {n_reachable} 条中, 黑盒可 fuzz 性")
    print("=" * 72)

    n_fuzzable = sum(cats[c] for c in BLACKBOX_FUZZABLE)
    n_not_fuzzable = sum(cats[c] for c in NOT_FUZZABLE_REASONS)
    print(f"  黑盒可 fuzz:    {n_fuzzable:>5} ({n_fuzzable/n_reachable*100:.1f}%)  [对照组]")
    print(f"  黑盒不可 fuzz:  {n_not_fuzzable:>5} ({n_not_fuzzable/n_reachable*100:.1f}%)  ← 我们的目标池")
    print()

    print("  黑盒不可 fuzz 的原因分解:")
    print("  " + "-" * 68)
    for cat, n in sorted(NOT_FUZZABLE_REASONS.items(), key=lambda kv: -cats[kv[0]]):
        print(f"    {cat:<26} {cats[cat]:>4} ({cats[cat]/n_reachable*100:4.1f}% of 可达)")
        print(f"      {NOT_FUZZABLE_REASONS[cat]}")
        print(f"      方案: {SOLUTIONS[cat]}")

    # ── 每类典型例子 ──
    print()
    print("=" * 72)
    print("每类典型例子 (各 3 条)")
    print("=" * 72)
    by_cat: dict[str, list] = defaultdict(list)
    for r in data:
        by_cat[r["category"]].append(r)
    for cat in list(NOT_FUZZABLE_REASONS) + list(BLACKBOX_FUZZABLE):
        print(f"\n  [{cat}] {cats[cat]} 条")
        for r in by_cat[cat][:3]:
            cls = r["class_name"].split(".")[-1]
            print(f"    - [{r['project']}] {cls}:{r['line_number']}")
            print(f"      {r['template'][:80]}")

    # ── 保存目标池 ──
    target_pool = [r for r in data if r["category"] in NOT_FUZZABLE_REASONS]
    with open("target_pool_http_reachable_not_blackbox.json", "w") as f:
        json.dump(target_pool, f, ensure_ascii=False, indent=2)
    print(f"\n目标池已保存: target_pool_http_reachable_not_blackbox.json ({len(target_pool)} 条)")

    # 保存对照组
    control = [r for r in data if r["category"] in BLACKBOX_FUZZABLE]
    with open("control_pool_blackbox_fuzzable.json", "w") as f:
        json.dump(control, f, ensure_ascii=False, indent=2)
    print(f"对照组已保存: control_pool_blackbox_fuzzable.json ({len(control)} 条)")


if __name__ == "__main__":
    main()
