"""
classify_sinks.py — 用 LLM 对黑盒不可 fuzz 的 sink 做"失败模式"分类

叙事: 我们有源码 → 黑盒 fuzz 不出来的 sink, 可以通过源码找到现成触发方案。
本脚本给每条 sink 归类"为什么黑盒难以触发/控制这条日志", 并统计各类别分布。

用法:
  uv run python classify_sinks.py --limit 20 --output sink_llm_classify_test20.json
  uv run python classify_sinks.py --output sink_llm_classify.json          # 全量
  uv run python classify_sinks.py --retry --output sink_llm_classify.json  # 补跑 API 错误条目
  uv run python classify_sinks.py --stats-only --output sink_llm_classify.json  # 只统计已有结果
"""

from __future__ import annotations

import argparse
import asyncio
import json
import glob
import os
import time
from collections import Counter
import httpx

SINKS_DIR = "sinks"
EXAMPLES_DIR = "examples"
EXCLUDE_PROJECTS = {"javams", "supermarket"}

BASE_URL = "https://llmapi.paratera.com/v1"
API_KEY = "sk-8Xjc92SiJM89Jj3UGS-r6Q"
MODEL = "Qwen3.8-27B"

# ── 分类体系: 为什么黑盒 fuzz 难以触发/控制这条日志 ──
CATEGORIES = {
    "db_state": "触发条件依赖数据库查询结果(唯一性检查/记录存在性/与DB字段值比较), 黑盒不知道需要什么种子数据",
    "cache_state": "触发条件依赖Redis或本地缓存中的状态, 黑盒无法得知缓存key与内部结构",
    "remote_service": "触发条件依赖远程调用(Feign/RPC/HTTP客户端)的返回值, 黑盒无法控制下游服务行为",
    "internal_state": "触发条件依赖应用内部状态/配置开关/环境变量/系统属性",
    "exception_path": "日志位于catch异常处理块, 需要先在内部制造特定异常才能到达",
    "non_http_entry": "所在方法不是HTTP请求入口(MQ消费者/定时任务/启动回调/仅被内部调用), 纯发HTTP请求根本到不了",
    "single_param_or_empty": "条件只检查单个请求参数或仅做空值校验(黑盒发请求其实能触发, 只是不满足多参数AND组合场景)",
    "printed_value_not_request": "条件可由请求触发, 但{}打印的值来自DB/内部状态而非当前请求参数, 日志内容不可控",
    "other": "以上都不是",
}

CATEGORY_PROMPT_LINES = "\n".join(f"- {k}: {v}" for k, v in CATEGORIES.items())

SYSTEM_PROMPT = f"""你是Java微服务安全分析专家。给定一条log语句及其源码上下文(>>>标记sink所在行), 判断: 纯黑盒fuzzing(只能发HTTP请求, 看不到源码)为什么难以触发或控制这条日志?

从以下类别中选最主要的一个(单选):
{CATEGORY_PROMPT_LINES}

判断要点:
- 先看sink所在的if条件块依赖什么: DB查询结果→db_state; Redis/缓存→cache_state; Feign/远程调用返回→remote_service; 配置项/内部状态→internal_state
- 再看方法如何被触发: 非HTTP入口(MQ监听/定时任务/启动回调/仅内部调用)→non_http_entry
- sink在catch块中→exception_path
- 若条件只是对单个请求参数做检查或空值校验(黑盒构造请求就能满足)→single_param_or_empty
- 若条件本身可由请求满足, 但{{}}打印的值来自DB/内部状态→printed_value_not_request
- 注意: 空值校验(hasEmpty/isBlank/isNull/==null)和OR连接的范围校验属于single_param_or_empty, 不算多参数AND组合
只输出JSON: {{"category":"类别名","reason":"一句话"}}"""

VALID_CATEGORIES = set(CATEGORIES.keys())

# ── 项目名 → examples 子目录映射 ──
PROJECT_DIR_MAP = {
    "apollo": "Apollo",
    "cloud-platform": "Cloud-Platform",
    "gulimall": "gulimall-learning",
    "lamp-cloud": "lamp-cloud",
    "light-reading-cloud": "novel-cloud",
    "mall4cloud": "mall4cloud",
    "mogu-blog": "MoGuBlog",
    "novel-cloud": "novel-cloud",
    "paascloud": "paascloud-master",
    "passjava": "PassJava-Platform",
    "pig": "pig",
    "piggymetrics": "PiggyMetrics",
    "ruoyi": "RuoYi-Cloud",
    "ruoyi-plus": "RuoYi-Cloud-Plus",
    "sitewhere": "SiteWhere",
    "small-swarm": "mall-swarm",
    "spring-boot-cloud": "spring-cloud-examples",
    "springblade": "SpringBlade",
    "youlai": "youlai-mall",
    "yudao": "yudao-cloud",
    "zlt": "zlt-microservices-platform",
}


def load_sinks() -> list[dict]:
    entries = []
    for f in sorted(glob.glob(os.path.join(SINKS_DIR, "*.json"))):
        base = os.path.basename(f)
        if any(ex in base for ex in EXCLUDE_PROJECTS):
            continue
        with open(f) as fh:
            data = json.load(fh)
        project = data.get("project", base.replace("-logging-sinks.json", ""))
        for r in data.get("results", []):
            tpl = r.get("template", "") or ""
            if "{}" not in tpl:
                continue
            entries.append({
                "project": project,
                "file_path": r.get("filePath", ""),
                "line_number": r.get("lineNumber", 0),
                "class_name": r.get("className", ""),
                "method_signature": r.get("methodSignature", ""),
                "level": r.get("level", ""),
                "template": tpl,
                "params": r.get("params", []),
                "raw_call": r.get("rawCallExpression", ""),
            })
    return entries


def read_source_context(project: str, file_path: str, line: int, **_kw) -> str:
    dir_name = PROJECT_DIR_MAP.get(project, project)
    full_path = os.path.join(EXAMPLES_DIR, dir_name, file_path)
    if not os.path.isfile(full_path):
        return ""
    try:
        with open(full_path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception:
        return ""
    end = min(len(lines), line)
    numbered = []
    for i, l in enumerate(lines[:end], 1):
        marker = ">>>" if i == line else "   "
        numbered.append(f"{marker} {i:4d} | {l.rstrip()}")
    return "\n".join(numbered)


def build_user_prompt(sink: dict, source: str) -> str:
    params_str = ", ".join(
        f"{p.get('expression','')} (type={p.get('type','')})"
        for p in sink["params"]
    )
    parts = [
        f"项目: {sink['project']}",
        f"文件: {sink['file_path']}:{sink['line_number']}",
        f"方法签名: {sink['method_signature']}",
        f"日志级别: {sink['level']}",
        f"日志模板: {sink['template']}",
        f"{{}}参数: {params_str}",
        f"原始调用: {sink['raw_call'][:200]}",
    ]
    if source:
        parts.append(f"\n源码上下文 (>>>标记sink所在行):\n{source}")
    else:
        parts.append("\n(源码不可用)")
    return "\n".join(parts)


async def classify_one(
    client: httpx.AsyncClient,
    sink: dict,
    sem: asyncio.Semaphore,
    idx: int,
    total: int,
) -> dict:
    source = read_source_context(sink["project"], sink["file_path"], sink["line_number"])
    user_prompt = build_user_prompt(sink, source)

    async with sem:
        for attempt in range(3):
            try:
                resp = await client.post(
                    f"{BASE_URL}/chat/completions",
                    json={
                        "model": MODEL,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt},
                        ],
                        "max_tokens": 4096,
                        "temperature": 0,
                    },
                    timeout=120,
                )
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                # 提取 JSON
                content = content.strip()
                if content.startswith("```"):
                    content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                result = json.loads(content)
                category = result.get("category", "other")
                if category not in VALID_CATEGORIES:
                    category = "other"
                print(f"  [{idx+1}/{total}] {category:<24} {sink['project']} {sink['class_name'].split('.')[-1]}:{sink['line_number']}")
                return {**sink, "category": category, "category_reason": result.get("reason", "")}
            except (httpx.HTTPStatusError, json.JSONDecodeError, KeyError) as e:
                if attempt < 2:
                    await asyncio.sleep(1 * (attempt + 1))
                    continue
                print(f"  [{idx+1}/{total}] ERROR {sink['project']} {sink['class_name'].split('.')[-1]}:{sink['line_number']} — {e}")
                return {**sink, "category": "error", "category_reason": f"API error: {e}"}
            except Exception as e:
                print(f"  [{idx+1}/{total}] ERROR {e}")
                return {**sink, "category": "error", "category_reason": f"error: {e}"}


async def main_async(sinks: list[dict], concurrency: int) -> list[dict]:
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient() as client:
        client.headers["Authorization"] = f"Bearer {API_KEY}"
        client.headers["Content-Type"] = "application/json"
        tasks = [classify_one(client, s, sem, i, len(sinks)) for i, s in enumerate(sinks)]
        return await asyncio.gather(*tasks)


def print_stats(results: list[dict]) -> None:
    counter = Counter(r.get("category", "error") for r in results)
    total = len(results)
    print(f"\n=== 分类分布 (共 {total} 条) ===")
    for cat, n in counter.most_common():
        desc = CATEGORIES.get(cat, "API 调用失败, 需补跑")
        print(f"  {cat:<26} {n:>5}  ({n/total*100:4.1f}%)  {desc}")

    # 按项目 × 类别交叉
    print("\n=== 项目 × 类别 ===")
    cross: dict[str, Counter] = {}
    for r in results:
        cross.setdefault(r["project"], Counter())[r.get("category", "error")] += 1
    cats = [c for c, _ in counter.most_common()]
    header = f"  {'project':<20}" + "".join(f"{c[:12]:>14}" for c in cats)
    print(header)
    for proj in sorted(cross, key=lambda p: -sum(cross[p].values())):
        row = f"  {proj:<20}" + "".join(f"{cross[proj].get(c, 0):>14}" for c in cats)
        print(row)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=30)
    parser.add_argument("--output", default="sink_llm_classify.json")
    parser.add_argument("--retry", action="store_true", help="只补跑上次 API 错误的条目")
    parser.add_argument("--stats-only", action="store_true", help="只统计已有结果, 不调用 API")
    args = parser.parse_args()

    if args.stats_only:
        with open(args.output) as f:
            results = json.load(f)
        print_stats(results)
        return

    if args.retry:
        with open(args.output) as f:
            prev = json.load(f)
        error_keys = set()
        ok_results = []
        for r in prev:
            if r.get("category") == "error" or "error" in r.get("category_reason", "").lower():
                error_keys.add((r["class_name"], r["line_number"]))
            else:
                ok_results.append(r)
        all_sinks = load_sinks()
        sinks = [s for s in all_sinks if (s["class_name"], s["line_number"]) in error_keys]
        print(f"补跑 API 错误条目: {len(sinks)}")
    else:
        sinks = load_sinks()
        ok_results = []
        print(f"载入含 {{}} 的 sink: {len(sinks)}")
        if args.limit > 0:
            sinks = sinks[:args.limit]
            print(f"限制检查: {len(sinks)}")

    print(f"模型: {MODEL}, 并发: {args.concurrency}")
    t0 = time.time()
    results = asyncio.run(main_async(sinks, args.concurrency))
    elapsed = time.time() - t0

    errors = [r for r in results if r.get("category") == "error"]
    print(f"\n完成: {len(results)} 条, 耗时 {elapsed:.1f}s, API错误: {len(errors)}")

    print_stats(results)

    # 抽样展示每类 2 条
    print("\n=== 每类抽样 ===")
    by_cat: dict[str, list[dict]] = {}
    for r in results:
        by_cat.setdefault(r.get("category", "error"), []).append(r)
    for cat in sorted(by_cat):
        for r in by_cat[cat][:2]:
            cls = r["class_name"].split(".")[-1]
            print(f"  [{cat}] {r['project']} {cls}:{r['line_number']}")
            print(f"    模板: {r['template'][:80]}")
            print(f"    理由: {r['category_reason']}")

    all_results = ok_results + results if args.retry else results
    with open(args.output, "w") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {args.output} (总计 {len(all_results)} 条)")


if __name__ == "__main__":
    main()
