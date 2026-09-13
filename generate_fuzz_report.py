#!/usr/bin/env python3
"""
generate_fuzz_report.py — 将 logs/fuzz 下的 per-sink Fuzz 对话 JSON 汇总成美观的单页 HTML 报告。

用法:
  python generate_fuzz_report.py                     # 扫描 logs/fuzz，每个 sink 取最新 JSON，输出 logs/fuzz/fuzz_report.html
  python generate_fuzz_report.py -o out.html         # 指定输出路径
  python generate_fuzz_report.py --all               # 每个 sink 保留所有历史 JSON（默认只取最新一份）
"""

from __future__ import annotations

import argparse
import html
import json
import re
from datetime import datetime
from pathlib import Path

FUZZ_DIR = Path(__file__).parent / "logs" / "fuzz"


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _duration(start: str | None, end: str | None) -> float | None:
    a, b = _parse_iso(start), _parse_iso(end)
    if a and b:
        return (b - a).total_seconds()
    return None


def load_records(all_history: bool) -> list[dict]:
    """加载 per-sink JSON。默认每个 sink 只取最新时间戳的一份。"""
    files = sorted(FUZZ_DIR.glob("*.json"))
    records: list[tuple[str, Path, dict]] = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if "sink" not in data or "calls" not in data:
            continue
        records.append((data.get("sink", f.stem), f, data))

    if all_history:
        return [d for _, _, d in records]

    # 每个 sink 取 mtime 最新的一份
    latest: dict[str, tuple[float, dict]] = {}
    for sink, f, data in records:
        mtime = f.stat().st_mtime
        if sink not in latest or mtime > latest[sink][0]:
            latest[sink] = (mtime, data)
    # 保持稳定顺序（按 sink 名）
    return [data for _, (_, data) in sorted(latest.items())]


def _split_response(resp: str) -> tuple[str, str]:
    """把 LLM 响应拆成 <think> 与 <json> 两部分。"""
    think = ""
    js = ""
    m = re.search(r"<think>(.*?)</think>", resp, re.DOTALL)
    if m:
        think = m.group(1).strip()
    m = re.search(r"<json>(.*?)</json>", resp, re.DOTALL)
    if m:
        js = m.group(1).strip()
    if not think and not js:
        js = resp.strip()
    return think, js


def _short_sink(sink: str) -> str:
    parts = sink.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else sink


def esc(s: object) -> str:
    return html.escape(str(s), quote=False)


def render_call(call: dict) -> str:
    rnd = call.get("round", "?")
    reached = call.get("reached_sink", False)
    found = call.get("marker_found", False)
    err = call.get("error")
    think, js = _split_response(call.get("response", ""))
    req = call.get("parsed_request")

    if err:
        badge = '<span class="pill pill-err">错误</span>'
    elif reached and found:
        badge = '<span class="pill pill-ok">命中 · marker 注入</span>'
    elif reached:
        badge = '<span class="pill pill-warn">到达 sink · 无 marker</span>'
    else:
        badge = '<span class="pill pill-miss">未到达</span>'

    req_html = ""
    if req:
        method = esc(req.get("method", ""))
        url = esc(req.get("url", ""))
        headers = req.get("headers") or {}
        body = req.get("body")
        hdr_lines = "".join(
            f'<div class="kv"><span class="k">{esc(k)}</span><span class="v">{esc(v)}</span></div>'
            for k, v in headers.items()
        ) or '<div class="muted">（无）</div>'
        body_html = (
            f'<pre class="code">{esc(json.dumps(body, ensure_ascii=False, indent=2))}</pre>'
            if body is not None
            else '<div class="muted">（无 body）</div>'
        )
        req_html = f"""
        <div class="req">
          <div class="req-line"><span class="method">{method}</span> <span class="url">{url}</span></div>
          <div class="sub">请求头</div>{hdr_lines}
          <div class="sub">Body</div>{body_html}
        </div>"""

    think_html = (
        f'<div class="sub">模型推理 &lt;think&gt;</div><pre class="think">{esc(think)}</pre>'
        if think
        else ""
    )
    js_html = (
        f'<div class="sub">模型输出 &lt;json&gt;</div><pre class="code">{esc(js)}</pre>'
        if js
        else ""
    )
    err_html = f'<div class="errbox">偏差 / 错误：{esc(err)}</div>' if err else ""

    return f"""
      <details class="round" {"open" if (reached and found) else ""}>
        <summary><span class="rnum">第 {esc(rnd)} 次尝试</span> {badge}</summary>
        <div class="round-body">
          {think_html}
          {js_html}
          {req_html}
          {err_html}
        </div>
      </details>"""


def render_record(rec: dict) -> str:
    sink = rec.get("sink", "?")
    api = rec.get("api_entry", "?")
    marker = rec.get("attack_marker", "")
    status = rec.get("status", "")
    rounds = rec.get("total_rounds", len(rec.get("calls", [])))
    dur = _duration(rec.get("start_time"), rec.get("end_time"))
    calls = rec.get("calls", [])

    success = any(c.get("reached_sink") and c.get("marker_found") for c in calls)
    if success:
        head_badge = '<span class="pill pill-ok">存在漏洞</span>'
        card_cls = "card ok"
    elif any(c.get("reached_sink") for c in calls):
        head_badge = '<span class="pill pill-warn">到达未注入</span>'
        card_cls = "card warn"
    else:
        head_badge = '<span class="pill pill-miss">未复现</span>'
        card_cls = "card miss"

    win = next(
        (c.get("round") for c in calls if c.get("reached_sink") and c.get("marker_found")),
        None,
    )
    meta = [
        f'<div class="m"><span>API 入口</span><b>{esc(api)}</b></div>',
        f'<div class="m"><span>攻击标记</span><b>{esc(marker)}</b></div>',
        f'<div class="m"><span>尝试次数</span><b>{esc(rounds)}</b></div>',
    ]
    if win:
        meta.append(f'<div class="m"><span>命中于</span><b>第 {esc(win)} 次</b></div>')
    if dur is not None:
        meta.append(f'<div class="m"><span>耗时</span><b>{dur:.1f}s</b></div>')

    rounds_html = "".join(render_call(c) for c in calls)

    return f"""
    <section class="{card_cls}">
      <div class="card-head">
        <div class="title">
          <div class="sink" title="{esc(sink)}">{esc(_short_sink(sink))}</div>
          <div class="sink-full">{esc(sink)}</div>
        </div>
        {head_badge}
      </div>
      <div class="meta">{"".join(meta)}</div>
      <div class="rounds">{rounds_html}</div>
    </section>"""


def build_html(records: list[dict]) -> str:
    total = len(records)
    vuln = sum(
        1
        for r in records
        if any(c.get("reached_sink") and c.get("marker_found") for c in r.get("calls", []))
    )
    reached_only = sum(
        1
        for r in records
        if not any(c.get("reached_sink") and c.get("marker_found") for c in r.get("calls", []))
        and any(c.get("reached_sink") for c in r.get("calls", []))
    )
    missed = total - vuln - reached_only
    gen_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pct = f"{(vuln / total * 100):.0f}%" if total else "0%"

    cards = "".join(render_record(r) for r in records)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Log Injection Fuzz 报告 · novel-cloud</title>
<style>
  :root {{
    --bg:#0f1419; --panel:#171d26; --panel2:#1e2530; --line:#2a3340;
    --txt:#e6edf3; --muted:#8b98a5; --accent:#58a6ff;
    --ok:#3fb950; --warn:#d29922; --miss:#6e7681; --err:#f85149;
  }}
  * {{ box-sizing:border-box; }}
  body {{
    margin:0; background:var(--bg); color:var(--txt);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
    line-height:1.6;
  }}
  .wrap {{ max-width:1080px; margin:0 auto; padding:32px 20px 80px; }}
  header h1 {{ font-size:26px; margin:0 0 4px; }}
  header .sub {{ color:var(--muted); font-size:14px; margin-bottom:24px; }}
  .stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:14px; margin-bottom:28px; }}
  .stat {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:16px 18px; }}
  .stat .num {{ font-size:30px; font-weight:700; }}
  .stat .lbl {{ color:var(--muted); font-size:13px; }}
  .stat.ok .num {{ color:var(--ok); }}
  .stat.warn .num {{ color:var(--warn); }}
  .stat.miss .num {{ color:var(--miss); }}
  .stat.rate .num {{ color:var(--accent); }}
  .card {{ background:var(--panel); border:1px solid var(--line); border-left-width:4px; border-radius:12px; padding:20px; margin-bottom:20px; }}
  .card.ok {{ border-left-color:var(--ok); }}
  .card.warn {{ border-left-color:var(--warn); }}
  .card.miss {{ border-left-color:var(--miss); }}
  .card-head {{ display:flex; align-items:flex-start; justify-content:space-between; gap:12px; }}
  .title .sink {{ font-size:18px; font-weight:700; }}
  .title .sink-full {{ color:var(--muted); font-size:12px; font-family:ui-monospace,Menlo,Consolas,monospace; }}
  .meta {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:10px; margin:16px 0; }}
  .meta .m {{ background:var(--panel2); border-radius:8px; padding:8px 12px; }}
  .meta .m span {{ display:block; color:var(--muted); font-size:12px; }}
  .meta .m b {{ font-size:14px; word-break:break-all; }}
  .pill {{ display:inline-block; padding:3px 12px; border-radius:999px; font-size:12px; font-weight:600; white-space:nowrap; }}
  .pill-ok {{ background:rgba(63,185,80,.15); color:var(--ok); }}
  .pill-warn {{ background:rgba(210,153,34,.15); color:var(--warn); }}
  .pill-miss {{ background:rgba(110,118,129,.2); color:var(--muted); }}
  .pill-err {{ background:rgba(248,81,73,.15); color:var(--err); }}
  .rounds {{ margin-top:8px; }}
  details.round {{ border:1px solid var(--line); border-radius:8px; margin-bottom:8px; overflow:hidden; }}
  details.round summary {{ cursor:pointer; padding:10px 14px; background:var(--panel2); display:flex; align-items:center; gap:10px; list-style:none; }}
  details.round summary::-webkit-details-marker {{ display:none; }}
  details.round summary::before {{ content:"\\25B6"; color:var(--muted); font-size:10px; transition:transform .15s; }}
  details.round[open] summary::before {{ transform:rotate(90deg); }}
  .rnum {{ font-weight:600; }}
  .round-body {{ padding:14px; }}
  .sub {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.5px; margin:12px 0 6px; }}
  .sub:first-child {{ margin-top:0; }}
  pre {{ margin:0; padding:12px 14px; border-radius:8px; overflow:auto; font-family:ui-monospace,Menlo,Consolas,monospace; font-size:12.5px; }}
  pre.code {{ background:#0d1117; border:1px solid var(--line); }}
  pre.think {{ background:#12161d; border:1px dashed var(--line); color:#c9d3de; white-space:pre-wrap; }}
  .req {{ background:var(--panel2); border-radius:8px; padding:12px 14px; }}
  .req-line {{ font-family:ui-monospace,Menlo,Consolas,monospace; font-size:13px; word-break:break-all; }}
  .method {{ color:var(--accent); font-weight:700; }}
  .kv {{ display:flex; gap:8px; font-family:ui-monospace,Menlo,Consolas,monospace; font-size:12.5px; }}
  .kv .k {{ color:var(--accent); }}
  .errbox {{ margin-top:10px; background:rgba(248,81,73,.08); border:1px solid rgba(248,81,73,.3); color:#ffb4ae; border-radius:8px; padding:10px 12px; font-size:13px; }}
  .muted {{ color:var(--muted); font-size:13px; }}
  footer {{ color:var(--muted); font-size:12px; text-align:center; margin-top:40px; }}
</style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>Log Injection Fuzz 报告</h1>
      <div class="sub">目标项目：novel-cloud · novel-book-service &nbsp;|&nbsp; 生成时间：{gen_time}</div>
    </header>
    <div class="stats">
      <div class="stat"><div class="num">{total}</div><div class="lbl">可达 Sink 总数</div></div>
      <div class="stat ok"><div class="num">{vuln}</div><div class="lbl">确认存在漏洞</div></div>
      <div class="stat warn"><div class="num">{reached_only}</div><div class="lbl">到达未注入</div></div>
      <div class="stat miss"><div class="num">{missed}</div><div class="lbl">未复现</div></div>
      <div class="stat rate"><div class="num">{pct}</div><div class="lbl">漏洞命中率</div></div>
    </div>
    {cards}
    <footer>由 generate_fuzz_report.py 自动生成 · Sink-Centric LLM Fuzz Pipeline</footer>
  </div>
</body>
</html>"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--output", default=str(FUZZ_DIR / "fuzz_report.html"))
    ap.add_argument("--all", action="store_true", help="包含每个 sink 的所有历史 JSON")
    args = ap.parse_args()

    records = load_records(args.all)
    if not records:
        print("未找到任何 per-sink Fuzz JSON（logs/fuzz/*.json）")
        return

    out = Path(args.output)
    out.write_text(build_html(records), encoding="utf-8")
    vuln = sum(
        1 for r in records
        if any(c.get("reached_sink") and c.get("marker_found") for c in r.get("calls", []))
    )
    print(f"报告已生成: {out}")
    print(f"  记录数: {len(records)}  确认漏洞: {vuln}")


if __name__ == "__main__":
    main()
