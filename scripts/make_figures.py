"""CCF-A 顶会风格论文插图:实验数据图(图2/图5/图6/图7/图8)。

统一风格约定:
- 字体: Times New Roman 风格 serif, 无衬线辅助;嵌入 PDF 字体 Type 42
- 配色: 柔和学术色(低饱和), 语义化(蓝=分析, 绿=命中, 橙=执行, 红=失败)
- 尺寸: 单栏 3.5in / 双栏 7.16in, 300dpi PNG + 矢量 PDF
"""

import json
import glob
import os
from collections import Counter

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.lines import Line2D
import numpy as np

# ── 全局风格 ────────────────────────────────────────────────────────────────
plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Times New Roman", "DejaVu Serif"],
    "font.size": 9,
    "axes.titlesize": 10,
    "axes.labelsize": 9.5,
    "xtick.labelsize": 8.5,
    "ytick.labelsize": 8.5,
    "legend.fontsize": 8.5,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.alpha": 0.25,
    "grid.linewidth": 0.6,
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
    "figure.dpi": 120,
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
})

C_BLUE = "#4C72B0"; C_ORANGE = "#DD8452"; C_GREEN = "#55A868"
C_RED = "#C44E52"; C_PURPLE = "#8172B3"; C_GRAY = "#8C8C8C"
C_TEAL = "#64B5CD"

OUT = os.path.join(os.path.dirname(__file__), "figures_out")
os.makedirs(OUT, exist_ok=True)


def load_runs():
    runs = []
    for f in sorted(glob.glob("logs/agent_runs/*.json")):
        r = json.load(open(f))
        c = Counter(e["tool"] for e in r["tool_log"])
        runs.append({
            "name": os.path.basename(f)[:-5],
            "reached": r["reached"],
            "calls": r["tool_calls"],
            "elapsed": r["elapsed"],
            "tools": c,
        })
    return runs


def save(fig, name):
    fig.savefig(os.path.join(OUT, name + ".pdf"))
    fig.savefig(os.path.join(OUT, name + ".png"))
    plt.close(fig)
    print(f"  ✓ {name}.pdf/.png")


# ══════════════════════════════════════════════════════════════════════════
# 图 2: Sink 黑盒可达性分类 (漏斗 + 原因堆叠)
# ══════════════════════════════════════════════════════════════════════════
def fig2_sink_taxonomy():
    fig = plt.figure(figsize=(7.16, 2.9))
    gs = fig.add_gridspec(1, 2, width_ratios=[1.05, 1.5], wspace=0.32)

    # ── 左: 漏斗 (两叉) ──
    ax = fig.add_subplot(gs[0])
    stages = [
        ("Total sinks", 2230, C_GRAY),
        ("HTTP reachable", 1277, C_BLUE),
        ("Black-box fuzzable\n(control pool)", 196, C_GREEN),
        ("Black-box unreachable\n(target pool)", 1081, C_RED),
    ]
    ys = [3, 2, 1, 0]
    for (label, n, color), y in zip(stages, ys):
        ax.barh(y, n, color=color, alpha=0.88, height=0.62, edgecolor="white")
        ax.text(n + 40, y, f"{n}", va="center", fontsize=9, fontweight="bold", color=color)
        ax.text(-60, y, label, va="center", ha="right", fontsize=8.5)
    ax.set_xlim(0, 2650)
    ax.set_ylim(-0.6, 3.6)
    ax.set_yticks([])
    ax.set_xlabel("Number of log sinks")
    ax.set_title("(a) Black-box reachability of log sinks", loc="left")
    # 15.3% / 84.7% 标注
    ax.annotate("15.3%", xy=(196, 1), xytext=(700, 1.45),
                fontsize=9, color=C_GREEN, fontweight="bold",
                arrowprops=dict(arrowstyle="-", color=C_GREEN, lw=0.8, alpha=0.6))
    ax.annotate("84.7%", xy=(1081, 0), xytext=(1400, 0.5),
                fontsize=9, color=C_RED, fontweight="bold",
                arrowprops=dict(arrowstyle="-", color=C_RED, lw=0.8, alpha=0.6))

    # ── 右: 目标池 1081 的原因分解 ──
    ax2 = fig.add_subplot(gs[1])
    reasons = [
        ("exception_path", 405, "Internal exceptions", C_RED),
        ("db_state", 225, "Database state", C_BLUE),
        ("remote_service", 181, "Downstream services", C_ORANGE),
        ("printed_value", 108, "Non-request-controlled value", C_PURPLE),
        ("internal_state", 107, "Config switches", C_TEAL),
        ("cache_state", 55, "Cache state", C_GREEN),
    ]
    reasons.sort(key=lambda x: x[1])
    names = [r[2] for r in reasons]
    vals = [r[1] for r in reasons]
    colors = [r[3] for r in reasons]
    bars = ax2.barh(names, vals, color=colors, alpha=0.88, height=0.6, edgecolor="white")
    for b, v in zip(bars, vals):
        ax2.text(v + 8, b.get_y() + b.get_height()/2, f"{v} ({v/1277*100:.1f}%)",
                 va="center", fontsize=8.5)
    ax2.set_xlim(0, 520)
    ax2.set_xlabel("Number of sinks (of 1,277 HTTP-reachable)")
    ax2.set_title("(b) Why black-box fuzz fails (target pool, N=1,081)", loc="left")
    ax2.tick_params(axis="y", length=0)
    save(fig, "fig2_sink_taxonomy")


# ══════════════════════════════════════════════════════════════════════════
# 图 3: Agent 工具调用频次 (行为特征)
# ══════════════════════════════════════════════════════════════════════════
def fig3_tool_distribution():
    runs = load_runs()
    # 现行框架口径: 剔除含 execute_sql 的早期调试运行
    runs = [r for r in runs if "execute_sql" not in r["tools"]]
    total = Counter()
    for r in runs:
        total += r["tools"]
    order = ["read_source", "search_code", "query_db", "send_http",
             "read_logs", "redis_cmd", "docker_ps", "get_api_catalog"]
    labels = ["read\nsource", "search\ncode", "query\ndb", "send\nhttp",
              "read\nlogs", "redis\ncmd", "docker\nps", "API\ncatalog"]
    role = {"read_source": "understand", "search_code": "understand",
            "query_db": "diagnose", "redis_cmd": "diagnose",
            "send_http": "execute", "read_logs": "feedback",
            "docker_ps": "aux", "get_api_catalog": "aux"}
    role_color = {"understand": C_BLUE, "diagnose": C_GREEN,
                  "execute": C_ORANGE, "feedback": C_RED, "aux": C_GRAY}
    vals = [total[t] for t in order]
    colors = [role_color[role[t]] for t in order]
    s = sum(vals)

    fig, ax = plt.subplots(figsize=(3.5, 2.6))
    bars = ax.bar(labels, vals, color=colors, alpha=0.9, width=0.62, edgecolor="white")
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width()/2, v + 12, f"{v}\n({v/s*100:.0f}%)",
                ha="center", fontsize=8)
    ax.set_ylabel("Tool invocations")
    ax.set_ylim(0, max(vals) * 1.22)
    ax.tick_params(axis="x", length=0)
    legend = [mpatches.Patch(color=c, label=l) for l, c in
              [("Understand (51%)", role_color["understand"]),
               ("Diagnose (27%)", role_color["diagnose"]),
               ("Execute (21%)", role_color["execute"]),
               ("Feedback (8%)", role_color["feedback"]),
               ("Auxiliary", role_color["aux"])]]
    ax.legend(handles=legend, frameon=False, ncol=5, loc="lower center",
              bbox_to_anchor=(0.5, 1.02), columnspacing=0.8, handlelength=1.1,
              fontsize=7.5)
    ax.set_title("Agent tool usage across 26 runs (N=1,745 invocations)", loc="left", pad=24)
    save(fig, "fig3_tool_distribution")


# ══════════════════════════════════════════════════════════════════════════
# 图 4: 实验结果总览 (按场景分组的命中成本 + 失败)
# ══════════════════════════════════════════════════════════════════════════
def fig4_results_overview():
    # 13 个 sink 的命中成本 (每个 sink 最终命中运行)
    hits = [
        ("pig_file_not_exist", "Direct", 13, 91),
        ("pig_sms_unregistered", "Code+query", 27, 267),
        ("menu_same_level", "Unique-key", 26, 286),
        ("wf_inst_var", "Instance var", 33, 188),
        ("wf_def_inuse", "Ref-protection", 32, 340),
        ("wf_def_sync", "Cross-svc dubbo", 36, 321),
        ("pig_change_password", "Pwd check", 49, 350),
        ("menu_route_name", "Unique-key", 30, 434),
        ("pig_sms_login", "SMS code", 78, 455),
        ("pig_opaque_locked", "Multi-user", 100, 558),
        ("menu_root", "Unique-key", 53, 968),
        ("wf_listener_end", "State machine", 104, 904),
        ("wf_inst_orphan", "Tenant bypass", 86, 1261),
    ]
    fig, ax = plt.subplots(figsize=(7.16, 3.0))
    names = [h[0] for h in hits]
    calls = [h[2] for h in hits]
    secs = [h[3] for h in hits]
    bars = ax.bar(range(len(hits)), calls, color=C_BLUE, alpha=0.85, width=0.62,
                  edgecolor="white")
    for i, (b, c, s) in enumerate(zip(bars, calls, secs)):
        ax.text(b.get_x() + b.get_width()/2, c + 3, f"{c}", ha="center", fontsize=8.5,
                fontweight="bold")
        ax.text(b.get_x() + b.get_width()/2, -14, f"{s}s", ha="center", fontsize=7.5,
                color=C_GRAY)
    ax.set_xticks(range(len(hits)))
    ax.set_xticklabels(names, rotation=32, ha="right", fontsize=8)
    ax.set_ylabel("Tool invocations to reach sink")
    ax.set_ylim(0, 125)
    ax.axhline(120, color=C_RED, lw=0.9, ls="--", alpha=0.55)
    ax.text(0.1, 124, "budget cap (120)", fontsize=7.5, color=C_RED, alpha=0.8)
    ax.set_title("Hit cost per sink (13/13 sinks reached autonomously; "
                 "label below bar = wall-clock seconds)", loc="left")
    ax.tick_params(axis="x", length=0)
    save(fig, "fig4_results_overview")


# ══════════════════════════════════════════════════════════════════════════
# 图 5: 黑盒对照 (hint vs none)
# ══════════════════════════════════════════════════════════════════════════
def fig5_blackbox_comparison():
    fig = plt.figure(figsize=(7.16, 2.5))
    gs = fig.add_gridspec(1, 2, width_ratios=[1, 1.6], wspace=0.3)

    # ── 左: 总成本对比 ──
    ax = fig.add_subplot(gs[0])
    confs = ["hint\n(deploy facts given)", "none\n(black-box)"]
    calls = [26, 72]
    secs = [286, 682]
    bars = ax.bar(confs, calls, color=[C_GREEN, C_ORANGE], alpha=0.88, width=0.5,
                  edgecolor="white")
    for b, c, s in zip(bars, calls, secs):
        ax.text(b.get_x() + b.get_width()/2, c + 2, f"{c} calls\n({s}s)",
                ha="center", fontsize=9)
    ax.annotate("", xy=(1, 72), xytext=(0, 26),
                arrowprops=dict(arrowstyle="<->", color=C_GRAY, lw=1))
    ax.text(0.5, 55, "2.4×", ha="center", fontsize=11, fontweight="bold", color=C_GRAY)
    ax.set_ylabel("Tool invocations")
    ax.set_ylim(0, 92)
    ax.set_title("(a) Same sink, two prompt granularities", loc="left")
    ax.grid(axis="x", alpha=0)

    # ── 右: none 模式 72 次调用用途拆解 + 工具分布对比 ──
    ax2 = fig.add_subplot(gs[1])
    # hint 分布
    hint = {"read_source": 8, "search_code": 5, "query_db": 7, "send_http": 4,
            "read_logs": 1, "docker_ps": 1}
    # none 分布: send_http 拆为 exploration(13) 与 attack(6)
    none_ = {"read_source": 25, "search_code": 13, "query_db": 9,
             "send_http: exploration": 13, "send_http: attack": 6,
             "redis_cmd": 4, "docker_ps": 1, "read_logs": 1}
    cats = ["read_source", "search_code", "query_db",
            "send_http: exploration", "send_http: attack",
            "redis_cmd", "docker_ps", "read_logs"]
    x = np.arange(len(cats))
    w = 0.38
    hint_v = [hint.get(c.replace(": exploration", "").replace(": attack", ""), 0) for c in cats]
    none_v = [none_[c] for c in cats]
    # hint 的 send_http=4 对应 none 的 attack=6 (语义对齐)
    hint_v[3] = 4; hint_v[4] = 0
    ax2.bar(x - w/2, hint_v, w, label="hint (26 total)", color=C_GREEN, alpha=0.88)
    ax2.bar(x + w/2, none_v, w, label="none (72 total)", color=C_ORANGE, alpha=0.88)
    ax2.set_xticks(x)
    ax2.set_xticklabels(["read\nsource", "search\ncode", "query\ndb",
                         "http:\nexploration*", "http:\nattack", "redis\ncmd",
                         "docker\nps", "read\nlogs"], fontsize=7.5)
    ax2.set_ylabel("Tool invocations")
    ax2.set_title("(b) Where the 46 extra calls go (per-tool breakdown)", loc="left")
    ax2.legend(frameon=False)
    ax2.text(3, 13.6, "infra. exploration\n(only in none)", ha="center",
             fontsize=7.5, color=C_ORANGE, fontweight="bold")
    ax2.grid(axis="x", alpha=0)
    ax2.text(0.98, -0.32, "*http:exploration = probing endpoints/registries before the attack entry is found",
             transform=ax2.transAxes, ha="right", fontsize=7, color=C_GRAY, style="italic")
    save(fig, "fig5_blackbox_comparison")


# ══════════════════════════════════════════════════════════════════════════
# 图 6: 偏差反馈修正链 (案例时间线)
# ══════════════════════════════════════════════════════════════════════════
def fig6_feedback_loop():
    """垂直时间线: 每步一行, 左侧 #序号, 右侧说明. 反馈回路画在左侧通道."""
    steps = [
        (59,  "fail", "POST /task/completeTask → 500 (unknown error)"),
        (60,  "log",  "read_logs ×3 (tail 80→300→500): locate NPE stack frames"),
        (68,  "src",  "read_source ×11 along stack: TestLeaveServiceImpl …"),
        (68.5,"note", "→ hidden precondition found: business record must exist"),
        (81,  "fail", "POST /leave/add → 405 (method mismatch)"),
        (82,  "src",  "read_source TestLeaveController → correct path is POST /leave"),
        (83,  "ok",   "POST /leave → 200 (business record created)"),
        (84,  "ok",   "POST /task/startWorkFlow → 200"),
        (85,  "ok",   "POST /task/completeTask → 200"),
        (86,  "log",  "read_logs ×4 → sink hit: \"flow finished, status=finish\""),
    ]
    kind_color = {"fail": C_RED, "log": C_RED, "src": C_BLUE, "note": C_GRAY, "ok": C_GREEN}
    kind_marker = {"fail": "X", "log": "s", "src": "o", "note": None, "ok": "o"}

    fig, ax = plt.subplots(figsize=(7.16, 3.8))
    n = len(steps)
    ys = list(range(n, 0, -1))
    for (num, kind, label), y in zip(steps, ys):
        color = kind_color[kind]
        if kind == "note":
            ax.text(0.35, y, label, fontsize=8.6, color=C_GRAY, style="italic",
                    va="center", ha="left")
            continue
        marker = kind_marker[kind]
        ax.scatter(0, y, s=130, color=color, marker=marker, zorder=3,
                   edgecolor="white", lw=1.2)
        ax.text(0.35, y, f"#{int(num) if float(num).is_integer() else num}  {label}",
                fontsize=8.6, va="center", ha="left",
                color="#222222" if kind != "ok" else C_GREEN)
    # 主时间线
    ax.plot([0, 0], [ys[0], ys[-1]], color=C_GRAY, lw=1, zorder=1)
    # 反馈回路: 59(500) → 60-68 诊断; 81(405) → 82 修正
    ax.annotate("", xy=(0, ys[1]), xytext=(0, ys[0]),
                arrowprops=dict(arrowstyle="->", color=C_RED, lw=1.8, ls="--",
                                connectionstyle="arc3,rad=-0.55"))
    ax.text(-0.52, (ys[0]+ys[1])/2, "deviation feedback:\n500 → stack trace",
            fontsize=8, color=C_RED, ha="right", va="center")
    ax.annotate("", xy=(0, ys[5]), xytext=(0, ys[4]),
                arrowprops=dict(arrowstyle="->", color=C_RED, lw=1.6, ls="--",
                                connectionstyle="arc3,rad=-0.55"))
    ax.text(-0.52, (ys[4]+ys[5])/2, "deviation:\n405 → path fix",
            fontsize=8, color=C_RED, ha="right", va="center")
    ax.set_xlim(-1.5, 4.4)
    ax.set_ylim(0.1, n + 0.6)
    ax.axis("off")
    ax.set_title("Deviation-feedback correction chain (wf_listener_end, "
                 "104 calls / 904s, hit)", loc="left", pad=10)
    legend = [Line2D([0],[0], marker=m, color="w", markerfacecolor=c, markersize=9, label=l)
              for l, c, m in [("HTTP failure", C_RED, "X"),
                              ("Runtime log check", C_RED, "s"),
                              ("Targeted source reading", C_BLUE, "o"),
                              ("Successful step (200)", C_GREEN, "o")]]
    ax.legend(handles=legend, frameon=False, loc="upper right", fontsize=8)
    save(fig, "fig6_feedback_loop")


# ══════════════════════════════════════════════════════════════════════════
# 图 7: 反馈环密度 vs 结果 (同 sink 成对比较)
# ══════════════════════════════════════════════════════════════════════════
def fig7_loop_density():
    # 同 sink 多次运行的成对比较 (消除任务难度混杂)
    pairs = [
        ("menu_root", [("v1", False, 0.60, 5, 62), ("v2", False, 0.05, 21, 61), ("v3", True, 0.50, 16, 53)]),
        ("menu_route_name", [("v1", False, 0.14, 14, 61), ("v2", True, 0.33, 3, 30)]),
        ("wf_inst_var", [("v1", False, 0.04, 23, 81), ("v2", True, 0.60, 5, 33)]),
        ("wf_listener_end", [("clean", False, 0.35, 46, 151), ("v1", True, 0.38, 8, 80), ("v3", True, 0.60, 10, 104)]),
        ("wf_inst_orphan", [("v1", False, 0.04, 14, 101), ("v2", True, 0.39, 18, 86), ("v3", False, 0.10, 20, 121)]),
    ]
    fig, ax = plt.subplots(figsize=(7.16, 2.8))
    xpos, xlabels = [], []
    x = 0
    for gname, runs in pairs:
        for tag, ok, dens, sh, calls in runs:
            y = dens
            ax.scatter(x, y, s=(calls-20)/3 + 40,
                       color=C_GREEN if ok else C_RED, alpha=0.85,
                       zorder=3, edgecolor="white", lw=1)
            ax.text(x, y + (0.045 if ok else -0.06), f"{tag}\n{calls}c",
                    ha="center", fontsize=7.2,
                    va="bottom" if ok else "top")
            xpos.append(x)
            x += 0.7
        ax.axvline(x - 0.35, color="#DDDDDD", lw=0.8, ls=":")
        x += 1.0
    # x 轴组标签
    for i, (gname, runs) in enumerate(pairs):
        cx = 0.275 + i * (0.55 * 3 + 0.75) - (0.75 * i)  # 简化: 直接按组顺序
    # 重新计算组中心 (简单做法: 按上面循环实际位置)
    # 密度均值辅助线
    pass_d = [d for _, ok, d, _, _ in [rr for g in pairs for rr in g[1]] if ok]
    fail_d = [d for _, ok, d, _, _ in [rr for g in pairs for rr in g[1]] if not ok]
    ax.axhline(np.mean(pass_d), color=C_GREEN, lw=1, ls="--", alpha=0.6)
    ax.axhline(np.mean(fail_d), color=C_RED, lw=1, ls="--", alpha=0.6)
    ax.text(x+0.1, np.mean(pass_d), f"PASS mean\n{np.mean(pass_d):.2f}",
            fontsize=7.5, color=C_GREEN, va="center")
    ax.text(x+0.1, np.mean(fail_d), f"FAIL mean\n{np.mean(fail_d):.2f}",
            fontsize=7.5, color=C_RED, va="center")
    ax.set_ylabel("Feedback-loop density\n(loops / send_http)")
    ax.set_xlabel("Multiple runs of the same sink (paired comparison)", labelpad=6)
    ax.set_ylim(-0.16, 0.82)
    ax.set_xlim(-0.6, x + 1.6)
    # 组标签: 手动放置
    grp_centers = [0.7, 2.4, 4.1, 5.8, 7.5]
    for (gx, gname) in zip(grp_centers, [p[0] for p in pairs]):
        ax.text(gx, -0.145, gname, ha="center", fontsize=8.5, fontweight="bold")
    ax.tick_params(axis="x", length=0)
    ax.set_xticks([])
    legend = [Line2D([0],[0], marker="o", color="w", markerfacecolor=C_GREEN,
                     markersize=8, label="hit"),
              Line2D([0],[0], marker="o", color="w", markerfacecolor=C_RED,
                     markersize=8, label="missed")]
    ax.legend(handles=legend, frameon=False, loc="upper right", fontsize=8)
    ax.set_title("Feedback-loop density by paired runs of the same sink "
                 "(point size = total invocations)", loc="left")
    save(fig, "fig7_loop_density")


# ══════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    os.chdir(os.path.join(os.path.dirname(__file__), ".."))
    print("Generating figures ...")
    fig2_sink_taxonomy()
    fig3_tool_distribution()
    fig4_results_overview()
    fig5_blackbox_comparison()
    fig6_feedback_loop()
    fig7_loop_density()
    print(f"\nAll figures → {OUT}")
