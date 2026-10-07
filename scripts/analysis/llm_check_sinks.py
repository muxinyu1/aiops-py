"""
llm_check_sinks.py — 用小模型并发检查 2227 条含 {} 的 sink 是否符合铁律

用法:
  uv run python llm_check_sinks.py                    # 全量检查
  uv run python llm_check_sinks.py --limit 50         # 只跑前 50 条
  uv run python llm_check_sinks.py --concurrency 20   # 并发数
"""

from __future__ import annotations

import argparse
import asyncio
import json
import glob
import os
import time
import httpx

SINKS_DIR = "sinks"
EXAMPLES_DIR = "examples"
EXCLUDE_PROJECTS = {"javams", "supermarket"}

BASE_URL = "https://llmapi.paratera.com/v1"
API_KEY = "sk-8Xjc92SiJM89Jj3UGS-r6Q"
MODEL = "Qwen3.8-27B"

SYSTEM_PROMPT = """判断log语句是否同时满足三条铁律:
1. {}占位符的值来自当前HTTP请求参数(非DB/Redis/远程调用结果/异常对象/内部状态)
2. log位于多请求参数AND组合条件块中(if用AND逻辑同时检查>=2个请求参数的具体值)。注意:空值校验(hasEmpty/isBlank/isNull/==null)和OR连接的范围校验不算组合条件
3. 条件和log打印的值都来自最终HTTP请求本身
只输出JSON: {"pass":true/false,"reason":"一句话"}"""

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


async def check_one(
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
                        "max_tokens": 800,
                        "temperature": 0,
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                # 提取 JSON
                content = content.strip()
                if content.startswith("```"):
                    content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                result = json.loads(content)
                print(f"  [{idx+1}/{total}] {'PASS' if result.get('pass') else 'FAIL'} {sink['project']} {sink['class_name'].split('.')[-1]}:{sink['line_number']}")
                return {**sink, "llm_pass": result.get("pass", False), "llm_reason": result.get("reason", "")}
            except (httpx.HTTPStatusError, json.JSONDecodeError, KeyError) as e:
                if attempt < 2:
                    await asyncio.sleep(1 * (attempt + 1))
                    continue
                print(f"  [{idx+1}/{total}] ERROR {sink['project']} {sink['class_name'].split('.')[-1]}:{sink['line_number']} — {e}")
                return {**sink, "llm_pass": False, "llm_reason": f"API error: {e}"}
            except Exception as e:
                print(f"  [{idx+1}/{total}] ERROR {e}")
                return {**sink, "llm_pass": False, "llm_reason": f"error: {e}"}


async def main_async(sinks: list[dict], concurrency: int) -> list[dict]:
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient() as client:
        client.headers["Authorization"] = f"Bearer {API_KEY}"
        client.headers["Content-Type"] = "application/json"
        tasks = [check_one(client, s, sem, i, len(sinks)) for i, s in enumerate(sinks)]
        return await asyncio.gather(*tasks)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=30)
    parser.add_argument("--output", default="sink_llm_results.json")
    parser.add_argument("--retry", action="store_true", help="只补跑上次 API 错误的条目")
    args = parser.parse_args()

    if args.retry:
        with open(args.output) as f:
            prev = json.load(f)
        error_keys = set()
        ok_results = []
        for r in prev:
            reason = r.get("llm_reason", "")
            if "error" in reason.lower() or "API" in reason:
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

    passed = [r for r in results if r.get("llm_pass")]
    failed = [r for r in results if not r.get("llm_pass")]

    print(f"\n完成: {len(results)} 条, 耗时 {elapsed:.1f}s")
    print(f"PASS: {len(passed)}")
    print(f"FAIL: {len(failed)}")

    if passed:
        print("\n=== PASS 列表 ===")
        for r in passed:
            cls = r["class_name"].split(".")[-1]
            print(f"  [{r['project']}] {cls}.{r['method_signature'].split('(')[0]} L{r['line_number']}")
            print(f"    模板: {r['template'][:80]}")
            print(f"    原因: {r['llm_reason']}")

    all_results = ok_results + results if args.retry else results
    with open(args.output, "w") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {args.output} (总计 {len(all_results)} 条)")


if __name__ == "__main__":
    main()
