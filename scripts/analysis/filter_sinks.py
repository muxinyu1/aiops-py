"""
filter_sinks.py — 规则化筛选 sink JSON 中符合铁律的候选

铁律:
  1. log 模板包含 {} 占位符
  2. {} 中的值来自当前 HTTP 请求参数 (非 DB/异常/内部状态)
  3. sink 位于多请求参数组合条件块中

用法:
  uv run python filter_sinks.py                # 筛选 + 统计
  uv run python filter_sinks.py --sample 30    # 筛选后从"淘汰"堆抽样验证
"""

from __future__ import annotations

import argparse
import json
import glob
import os
import random
import re
from dataclasses import dataclass, field

SINKS_DIR = "sinks"
EXCLUDE_PROJECTS = {"javams", "supermarket"}

# ═══════════════════════════════════════════════════════════════════════════════
# 规则定义
# ═══════════════════════════════════════════════════════════════════════════════

# ── 负面规则: 命中则淘汰 ──

EXCEPTION_TYPES = re.compile(
    r"(Exception|Throwable|Error|RuntimeException|IOException"
    r"|IllegalArgumentException|IllegalStateException|NullPointerException"
    r"|SQLException|TimeoutException)$", re.I
)

EXCEPTION_EXPRESSIONS = re.compile(
    r"^(e|ex|err|error|t|cause|exception|throwable"
    r"|e\d|ex\d|ioException|sqlException)$", re.I
)

# DB/远程调用返回对象的 getter — 高概率是从数据库取的值
DB_ENTITY_GETTER = re.compile(
    r"^(user|userInfo|account|entity|record|row|dbObj|sysMenu|menu|dept"
    r"|role|config|result|data|info|model|po|domain|bean|obj|item|order"
    r"|member|tenant|client|app|namespace|cluster|release|commit"
    r"|clientVo|userVo|sysUser|adminUser|loginUser|baseUser"
    r"|passwordErrorNum|maxPasswordErrorNum|tempCode"
    r"|ipSendTimes|mobileSmsCount|ipSmsCount|sendSmsRateCount"
    r")\.(get|is|toString|getName|getId|getCode|getStatus|getType"
    r"|getMenuName|getPath|getRouteName|getMenuType|getParentId"
    r"|getNickName|getUsername|getAccount|getPassword|getEmail|getPhone"
    r"|getAppId|getClientId|getMessage|getDescription).*",
    re.I
)

# 内部状态/常量/计算值
INTERNAL_STATE = re.compile(
    r"^(this\.|Thread\.currentThread|System\.|Runtime\."
    r"|UUID\.|Math\.|String\.valueOf|Integer\.valueOf"
    r"|LocalDateTime\.|DateUtil\.|DateUtils\."
    r"|request\.getRemoteAddr|request\.getRequestURI|request\.getMethod"
    r"|WebUtil\.getIP|IpUtil|RequestUtils\.getIP|HttpUtil\.getIP"
    r"|WebUtils\.getIP|getRemoteAddr|getClientIp"
    r"|response\.|httpServletResponse\."
    r"|stopWatch|timer|watch|sw\.)"
    r".*", re.I
)

# toString/getMessage 通常是异常或对象
EXCEPTION_MSG = re.compile(
    r"\.(getMessage|getLocalizedMessage|getCause|getStackTrace"
    r"|printStackTrace|toString)\(\)$", re.I
)

# 纯数值/size/count — 多半是 DB 查询结果或内部计算
COUNT_PATTERNS = re.compile(
    r"^(count|total|size|length|num|index|page|pageSize|pageNum"
    r"|offset|limit|maxRetries|retryCount|retryTimes|elapsed"
    r"|cost|duration|time|startTime|endTime|timeout)$", re.I
)

# 模板特征: 纯异常/启动/关闭日志
TEMPLATE_NOISE = re.compile(
    r"(starting\.\.\.|started\.\.\.|shutdown|shutting down|initializ"
    r"|destroying|disposed|closed connection|heartbeat"
    r"|scheduled task|cron|timer fired|async task"
    r"|bean .* registered|registering bean|creating bean"
    r"|loading config|loaded|reloading"
    r"|cache (hit|miss|evict|expire|refresh|clear|put|get)"
    r"|retry (attempt|after|sleeping|backoff)"
    r"|circuit.?breaker|fallback|degrade)", re.I
)

# catch 块特征: rawCallExpression 通常在 catch 中
CATCH_PATTERN = re.compile(
    r"(catch|exception|error|fail|unable|cannot|could not"
    r"|unexpected|invalid response|timeout|timed out"
    r"|connection refused|unreachable)", re.I
)

# ── 正面规则: 命中加分 ──

# 方法签名包含 DTO/BO/VO/Form/Request/Body/Param 类型
REQUEST_TYPE_IN_SIG = re.compile(
    r"(Dto|BO|Vo|Form|Request|Body|Param|Command|Query|Input"
    r"|LoginBody|LoginParam|RegisterBody|SysMenu|SysUser|SysRole"
    r"|Map<String)", re.I
)

# 参数 expression 直接是简单变量名 (非 getter, 非链式调用)
SIMPLE_VAR = re.compile(r"^[a-zA-Z_]\w{0,30}$")

# 参数 expression 含 body.get / dto.get / request.getParameter
DIRECT_REQUEST_ACCESS = re.compile(
    r"(body\.get|dto\.get|bo\.get|vo\.get|form\.get|param\.get"
    r"|request\.getParameter|request\.getHeader|loginBody\.get"
    r"|args\.get|args\.getStr|tokenParameter|requestBody)", re.I
)

# 方法名暗示业务验证/检查 (多条件场景)
VALIDATION_METHOD = re.compile(
    r"(check|valid|verify|evaluat|assess|inspect|audit|review"
    r"|compar|match|conflict|duplicat|unique|exist|repeat"
    r"|login|auth|register|create|add|insert|update|save"
    r"|submit|process|handle|execute|invoke|dispatch"
    r"|import|export|upload|download|transfer|convert"
    r"|send|notify|publish|broadcast|push)", re.I
)


# ═══════════════════════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class SinkEntry:
    project: str
    file_path: str
    line_number: int
    class_name: str
    method_signature: str
    method_name: str  # log 方法名 (info/warn/error)
    level: str
    template: str
    params: list[dict]
    raw_call: str
    reject_reasons: list[str] = field(default_factory=list)
    positive_signals: list[str] = field(default_factory=list)
    score: int = 0


# ═══════════════════════════════════════════════════════════════════════════════
# 规则引擎
# ═══════════════════════════════════════════════════════════════════════════════

def load_sinks() -> list[SinkEntry]:
    entries = []
    for f in sorted(glob.glob(os.path.join(SINKS_DIR, "*.json"))):
        base = os.path.basename(f)
        if any(ex in base for ex in EXCLUDE_PROJECTS):
            continue
        with open(f) as fh:
            data = json.load(fh)
        project = data.get("project", base.replace("-logging-sinks.json", ""))
        results = data.get("results", []) if isinstance(data, dict) else data
        for r in results:
            tpl = r.get("template", "") or ""
            if "{}" not in tpl:
                continue
            entries.append(SinkEntry(
                project=project,
                file_path=r.get("filePath", ""),
                line_number=r.get("lineNumber", 0),
                class_name=r.get("className", ""),
                method_signature=r.get("methodSignature", ""),
                method_name=r.get("methodName", ""),
                level=r.get("level", ""),
                template=tpl,
                params=r.get("params", []),
                raw_call=r.get("rawCallExpression", ""),
            ))
    return entries


def count_placeholders(template: str) -> int:
    return template.count("{}")


def apply_rules(sink: SinkEntry) -> None:
    """对单条 sink 应用全部规则，填充 reject_reasons / positive_signals / score。"""
    reject = sink.reject_reasons
    pos = sink.positive_signals
    score = 0

    ph_count = count_placeholders(sink.template)
    non_exception_params = []

    # ── R1: 分析每个 {} 对应的 param ──
    for p in sink.params:
        expr = p.get("expression", "") or ""
        ptype = p.get("type", "") or ""

        # R1a: 异常类型 param
        if EXCEPTION_TYPES.search(ptype):
            continue  # 不计入有效 param
        if EXCEPTION_EXPRESSIONS.match(expr):
            continue

        # R1b: .getMessage() 等
        if EXCEPTION_MSG.search(expr):
            reject.append(f"R1b: exception_msg [{expr}]")
            continue

        # R1c: DB entity getter
        if DB_ENTITY_GETTER.match(expr):
            reject.append(f"R1c: db_getter [{expr}]")
            continue

        # R1d: 内部状态
        if INTERNAL_STATE.match(expr):
            reject.append(f"R1d: internal [{expr}]")
            continue

        # R1e: 纯计数值
        if COUNT_PATTERNS.match(expr):
            reject.append(f"R1e: count_var [{expr}]")
            continue

        non_exception_params.append(expr)

    # ── R2: 所有 {} 对应的 param 全部被排除 → 无有效请求参数 ──
    if ph_count > 0 and len(non_exception_params) == 0:
        reject.append("R2: no_valid_params (all exception/db/internal)")

    # ── R3: 模板噪声 ──
    if TEMPLATE_NOISE.search(sink.template):
        reject.append(f"R3: template_noise [{sink.template[:50]}]")

    # ── R4: 方法签名无参数 或 签名不含请求类型 ──
    sig = sink.method_signature
    # 提取参数列表
    sig_match = re.search(r"\(([^)]*)\)", sig)
    sig_params_str = sig_match.group(1).strip() if sig_match else ""
    if not sig_params_str:
        reject.append("R4a: no_method_params")
    else:
        sig_param_list = [p.strip() for p in sig_params_str.split(",") if p.strip()]
        # 排除 HttpServletRequest/Response, Model, BindingResult 等框架参数
        framework_types = re.compile(
            r"(HttpServletRequest|HttpServletResponse|Model|BindingResult"
            r"|RedirectAttributes|Authentication|Principal|Locale|TimeZone"
            r"|MultipartFile|OutputStream|InputStream|Writer|Reader"
            r"|Pageable|Sort|PageRequest)", re.I
        )
        biz_params = [p for p in sig_param_list if not framework_types.search(p)]

        if len(biz_params) < 2:
            reject.append(f"R4b: single_biz_param ({len(biz_params)})")

        # 正面: 签名含 DTO/BO/VO 等请求对象
        if REQUEST_TYPE_IN_SIG.search(sig_params_str):
            pos.append("P1: request_type_in_sig")
            score += 2

    # ── R5: rawCallExpression 含 catch/exception 关键字模板 ──
    if CATCH_PATTERN.search(sink.template) and not any("R" not in r for r in reject):
        reject.append(f"R5: catch_template [{sink.template[:40]}]")

    # ── 正面信号 ──

    # P2: 非异常 param 是简单变量名且出现在方法签名中
    for expr in non_exception_params:
        if SIMPLE_VAR.match(expr) and expr in sig:
            pos.append(f"P2: param_from_sig [{expr}]")
            score += 3

    # P3: param 直接从 request/body/dto 取值
    for expr in non_exception_params:
        if DIRECT_REQUEST_ACCESS.search(expr):
            pos.append(f"P3: direct_request [{expr}]")
            score += 3

    # P4: 方法名暗示业务验证
    class_method = sig.split("(")[0] if "(" in sig else sig
    if VALIDATION_METHOD.search(class_method):
        pos.append(f"P4: validation_method [{class_method}]")
        score += 1

    # P5: 多个非异常 param (暗示多参数场景)
    if len(non_exception_params) >= 2:
        pos.append(f"P5: multi_params ({len(non_exception_params)})")
        score += 2

    # P6: WARN/ERROR 级别 (比 INFO/DEBUG 更可能在条件块中)
    if sink.level in ("WARN", "ERROR"):
        pos.append("P6: warn_or_error")
        score += 1

    sink.score = score


def classify(sink: SinkEntry) -> str:
    """返回 'CANDIDATE' / 'MAYBE' / 'REJECT'。"""
    has_hard_reject = any(r.startswith("R2:") or r.startswith("R4a:") for r in sink.reject_reasons)
    soft_rejects = len(sink.reject_reasons)

    if has_hard_reject and sink.score < 5:
        return "REJECT"
    if soft_rejects >= 2 and sink.score < 3:
        return "REJECT"
    if sink.score >= 5:
        return "CANDIDATE"
    if sink.score >= 2:
        return "MAYBE"
    return "REJECT"


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=0,
                        help="从 REJECT 堆中随机抽样 N 条输出详情")
    parser.add_argument("--show-candidates", action="store_true",
                        help="输出全部 CANDIDATE 详情")
    parser.add_argument("--show-maybe", action="store_true",
                        help="输出全部 MAYBE 详情")
    args = parser.parse_args()

    sinks = load_sinks()
    print(f"载入含 {{}} 的 sink 总数: {len(sinks)}")

    for s in sinks:
        apply_rules(s)

    candidates = [s for s in sinks if classify(s) == "CANDIDATE"]
    maybes = [s for s in sinks if classify(s) == "MAYBE"]
    rejects = [s for s in sinks if classify(s) == "REJECT"]

    print(f"\nCANDIDATE (高可能符合): {len(candidates)}")
    print(f"MAYBE    (需人工确认): {len(maybes)}")
    print(f"REJECT   (规则淘汰):   {len(rejects)}")

    # 按项目统计
    from collections import Counter
    print("\n=== CANDIDATE 按项目分布 ===")
    for proj, cnt in Counter(s.project for s in candidates).most_common():
        print(f"  {proj}: {cnt}")

    print("\n=== MAYBE 按项目分布 ===")
    for proj, cnt in Counter(s.project for s in maybes).most_common():
        print(f"  {proj}: {cnt}")

    def print_sink(s: SinkEntry, idx: int = 0):
        label = classify(s)
        print(f"\n{'─'*70}")
        print(f"  #{idx} [{label}] score={s.score}")
        print(f"  项目: {s.project}")
        print(f"  文件: {s.file_path}:{s.line_number}")
        print(f"  签名: {s.method_signature}")
        print(f"  级别: {s.level}")
        print(f"  模板: {s.template}")
        print(f"  参数: {[p.get('expression','') for p in s.params]}")
        print(f"  调用: {s.raw_call[:120]}")
        if s.reject_reasons:
            print(f"  淘汰: {s.reject_reasons}")
        if s.positive_signals:
            print(f"  正面: {s.positive_signals}")

    if args.show_candidates:
        print(f"\n{'═'*70}")
        print(f"=== 全部 CANDIDATE ({len(candidates)}) ===")
        for i, s in enumerate(candidates, 1):
            print_sink(s, i)

    if args.show_maybe:
        print(f"\n{'═'*70}")
        print(f"=== 全部 MAYBE ({len(maybes)}) ===")
        for i, s in enumerate(maybes, 1):
            print_sink(s, i)

    if args.sample > 0:
        n = min(args.sample, len(rejects))
        sampled = random.sample(rejects, n)
        print(f"\n{'═'*70}")
        print(f"=== REJECT 随机抽样 ({n}/{len(rejects)}) ===")
        for i, s in enumerate(sampled, 1):
            print_sink(s, i)


if __name__ == "__main__":
    main()
