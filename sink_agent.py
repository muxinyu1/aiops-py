"""
sink_agent.py — Sink-directed LLM 灰盒 Agent

核心思想: agent 通过工具调用自主完成 sink 到达的全过程 —
读源码 → 诊断前置条件 → 合成 setup (DB种子/缓存/配置) → 构造请求 → 读日志偏差反馈 → 修正 → 命中 sink。

与人工 setup 的 demo (demo_fuzz_ruoyi_plus.py) 的区别: 所有前置知识由 agent 从源码挖出, 无人工 setup。

用法:
  source .env && uv run python sink_agent.py --sink ruoyi_plus_menu_same_level
  uv run python sink_agent.py --list
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

# ═══════════════════════════════════════════════════════════════════════════════
# 工具实现 — agent 的全部能力边界
# ═══════════════════════════════════════════════════════════════════════════════

MAX_TOOL_RESULT_CHARS = 8000
SQL_DANGEROUS = ("DROP", "TRUNCATE", "ALTER", "CREATE", "GRANT", "SHUTDOWN", "DELETE FROM user")


def _truncate(s: str, limit: int = MAX_TOOL_RESULT_CHARS) -> str:
    return s if len(s) <= limit else s[:limit] + f"\n...[截断, 共{len(s)}字符]"


def tool_read_source(path: str, start: int, end: int) -> str:
    """读取项目源码文件片段。path 相对于 examples/。"""
    root = os.path.join("examples")
    full = os.path.join(root, path.lstrip("/"))
    if not os.path.isfile(full):
        return f"ERROR: 文件不存在: {path}"
    with open(full, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    start = max(1, int(start))
    end = min(len(lines), int(end))
    if start > end:
        return f"ERROR: start({start}) > end({end}), 文件共 {len(lines)} 行"
    numbered = [f"{i:4d} | {lines[i-1].rstrip()}" for i in range(start, end + 1)]
    return _truncate(f"{path} (L{start}-{end}, 共{len(lines)}行)\n" + "\n".join(numbered))


def tool_search_code(pattern: str, path_prefix: str = "") -> str:
    """在项目源码中 grep 模式 (python regex), 返回匹配行。"""
    try:
        cmd = ["grep", "-rn", "-E", pattern, "examples", "--include=*.java", "--include=*.xml", "--include=*.yml", "--include=*.sql"]
        if path_prefix:
            cmd += [f"examples/{path_prefix.lstrip('/')}"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        out = r.stdout or ""
        return _truncate(out if out.strip() else f"无匹配: {pattern}")
    except subprocess.TimeoutExpired:
        return "ERROR: grep 超时 (模式过宽?)"


def tool_query_db(sql: str) -> str:
    """只读 SQL: 查询 schema / 现有数据。"""
    banned = ("INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER", "CREATE", "GRANT", "SHUTDOWN")
    if any(re.search(r'\b' + b + r'\b', sql.upper()) for b in banned):
        return "ERROR: query_db 仅允许只读 SELECT/SHOW/DESC 语句, 写操作请用 execute_sql"
    return _exec_mysql(sql)


def tool_execute_sql(sql: str) -> str:
    """执行写 SQL (INSERT/UPDATE/DELETE): 注入种子数据。危险语句被拦截。"""
    up = sql.upper()
    if any(b in up for b in SQL_DANGEROUS):
        return f"ERROR: 危险语句被拦截"
    if not (up.startswith("INSERT") or up.startswith("UPDATE") or up.startswith("DELETE")):
        return "ERROR: execute_sql 仅允许 INSERT/UPDATE/DELETE"
    return _exec_mysql(sql)


def _exec_mysql(sql: str) -> str:
    db = os.environ.get("AGENT_MYSQL_DB", "ruoyi-vue-pro")
    cmd = ["docker", "exec", os.environ.get("AGENT_MYSQL_CONTAINER", "trace-real-mysql"),
           "mysql", "-uroot", "-p123456", db, "-e", sql]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        out = r.stdout or ""
        err = r.stderr or ""
        # mysql client 把表格输出打到 stdout, warning 打到 stderr (密码警告可忽略)
        err_lines = [l for l in err.splitlines() if "password on the command" not in l and l.strip()]
        result = out
        if err_lines:
            result += ("\n[stderr] " + "\n[stderr] ".join(err_lines[:5]))
        return _truncate(result.strip() if result.strip() else "(空结果, 执行成功)")
    except subprocess.TimeoutExpired:
        return "ERROR: mysql 超时"


def tool_redis_cmd(cmd_str: str) -> str:
    """向 Redis 发送命令 (预置缓存状态)。"""
    container = os.environ.get("AGENT_REDIS_CONTAINER", "trace-real-redis")
    password = os.environ.get("AGENT_REDIS_PASSWORD", "password")
    parts = cmd_str.split()
    cmd = ["docker", "exec"]
    if password:
        cmd += ["-e", f"REDISCLI_AUTH={password}"]
    cmd += [container, "redis-cli", "--raw", *parts]
    try:
        r = subprocess.run(cmd,
                           capture_output=True, text=True, timeout=15)
        return _truncate((r.stdout or r.stderr or "(空)").strip())
    except subprocess.TimeoutExpired:
        return "ERROR: redis 超时"


def tool_read_logs(container: str, tail: int) -> str:
    """读取目标服务容器日志末尾 N 行 (pig 单体无容器, 传文件路径则读宿主机日志文件)。"""
    if container and container.endswith(".log"):
        try:
            with open(container) as f:
                lines = f.readlines()
            return _truncate("".join(lines[-int(tail):])[-MAX_TOOL_RESULT_CHARS:])
        except OSError as e:
            return f"ERROR: 读日志文件失败: {e}"
    r = subprocess.run(["docker", "logs", "--tail", str(int(tail)), container],
                       capture_output=True, text=True, timeout=30)
    return _truncate((r.stdout + r.stderr)[-MAX_TOOL_RESULT_CHARS:])


_PIG_AES_KEY = b"thanks,pig4cloud"  # pig security.encode-key (16 字节)


def _pig_aes_encrypt(plaintext: str) -> str:
    """pig 客户端密码加密: AES/CFB/NoPadding, key=iv, base64 输出 (与 PasswordDecoderFilter 对应)。"""
    import base64
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    c = Cipher(algorithms.AES(_PIG_AES_KEY), modes.CFB(_PIG_AES_KEY)).encryptor()
    return base64.b64encode(c.update(plaintext.encode())).decode()


def _looks_like_base64_aes(value: str) -> bool:
    """判断密码值是否已是 base64 加密串。明文密码通常是简单词; 密文是 24 位且含 +/= 的 base64。"""
    if len(value) < 20:
        return False
    if "=" not in value and "+" not in value and "/" not in value:
        return False
    import base64 as b64
    try:
        b64.b64decode(value, validate=True)
        return True
    except Exception:
        return False


def tool_send_http(method: str, url: str, headers_json: str, body: str) -> str:
    """发送 HTTP 请求 (fuzz 的主动作)。"""
    # urllib 会拒绝 URL 中的尖括号/空格等非法字符，而 agent 经常把
    # <<SINK_REACHED>> 直接放进 query 参数。编码后由服务端正常解码，避免
    # 请求在进入 Spring Controller 之前就被 Tomcat 以 HTTP 400 拒绝。
    url = urllib.parse.quote(url, safe=":/?&=#%+;,[]@")
    headers = json.loads(headers_json) if headers_json else {}
    # 工具层规范化: 通配符 Content-Type 会被 Spring 拒收 (IllegalArgumentException),
    # 这是 HTTP 客户端的正确性修复, 与 agent 策略无关
    ct = headers.get("Content-Type", headers.get("content-type", ""))
    if "*" in ct:
        headers["Content-Type"] = "application/json"
    # 工具层协议适配: pig 的登录端点要求密码 AES/CFB 加密 (客户端加密传输),
    # form 表单里 password 为明文时自动加密 — 与 Content-Type 规范化同类
    if "/oauth2/token" in url and body and "grant_type=password" in body and "password=" in body:
        try:
            from urllib.parse import parse_qs, urlencode
            params = parse_qs(body, keep_blank_values=True)
            raw_pwd = params.get("password", [""])[0]
            if raw_pwd and not _looks_like_base64_aes(raw_pwd):
                enc = _pig_aes_encrypt(raw_pwd)
                params["password"] = [enc]
                body = urlencode({k: v[0] for k, v in params.items()})
                data = body.encode()
        except Exception:
            pass
    data = body.encode() if body else None
    req = urllib.request.Request(url, method=method.upper(), headers=headers, data=data)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
            content = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        status = e.code
        content = e.read().decode("utf-8", errors="replace")
        resp = e  # HTTPError 也带 headers, 统一用 resp 变量避免 UnboundLocalError
    except Exception as e:
        return f"HTTP ERROR: {e}"
    # 工具层修复: 不可见字符 (NBSP/控制字符) 会在 agent 复制长字符串时丢失,
    # 转义为可见形式保证 token 等长字符串可被忠实复制
    content = content.replace("\u00a0", "\\u00a0")
    content = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", lambda m: f"\\x{ord(m.group(0)):02x}", content)
    hdrs = "\n".join(f"{k}: {v}" for k, v in resp.headers.items() if k.lower() in ("content-type",))
    result = _truncate(f"HTTP {status}\n{hdrs}\n\n{content}")
    # 工具层会话自愈: LLM 转录 400+ 字符 JWT 易丢字符 → 401。
    # 记录最近一次登录成功响应中的 access_token; 若带 Bearer 的请求收到 401,
    # 用该 token 自动重试一次 (HTTP 客户端会话修复, 与 Content-Type 规范化同类)。
    global _LAST_LOGIN_TOKEN
    m_tok = re.search(r'"access_token"\s*:\s*"([^"]+)"', content)
    if m_tok and ("/login" in url or "/auth/login" in url or "/oauth" in url):
        _LAST_LOGIN_TOKEN = m_tok.group(1)
    # RuoYi 网关风格: HTTP 200 + body {"code":401}; 标准风格: HTTP 401 — 两种都算鉴权失败
    auth_failed = status == 401 or re.search(r'"code"\s*:\s*401', content) is not None
    if auth_failed and _LAST_LOGIN_TOKEN and "Authorization" in headers:
        retry_headers = dict(headers)
        retry_headers["Authorization"] = f"Bearer {_LAST_LOGIN_TOKEN}"
        req2 = urllib.request.Request(url, method=method.upper(), headers=retry_headers, data=data)
        try:
            with urllib.request.urlopen(req2, timeout=15) as resp2:
                status2 = resp2.status
                content2 = resp2.read().decode("utf-8", errors="replace")
            note = f"\n\n[工具层自动重试: 原请求鉴权失败 (token 转录可能损坏), 已用最近登录 token 重试 → HTTP {status2}]"
            return _truncate(f"HTTP {status2}\n\n{content2}{note}")
        except urllib.error.HTTPError as e2:
            return _truncate(f"HTTP {e2.code}\n\n{e2.read().decode('utf-8', errors='replace')}\n\n[工具层自动重试: 原请求鉴权失败, 重试后仍 {e2.code}]")
    return result


_LAST_LOGIN_TOKEN = ""
def tool_docker(args_prompt: str = "") -> str:
    """列出运行中的容器, 帮 agent 找到目标服务容器名。"""
    r = subprocess.run(["docker", "ps", "--format", "{{.Names}}\t{{.Status}}\t{{.Image}}"],
                       capture_output=True, text=True, timeout=15)
    return _truncate(r.stdout or r.stderr or "(无容器)")


# ── B 方案: API 目录工具 (静态分析产物 api_table_map.json) ──

_API_TABLE_MAP: dict | None = None


def _load_api_table_map() -> dict:
    global _API_TABLE_MAP
    if _API_TABLE_MAP is None:
        path = "data/api_table_map.json"
        if os.path.isfile(path):
            _API_TABLE_MAP = json.load(open(path))
        else:
            _API_TABLE_MAP = {}
    return _API_TABLE_MAP


def tool_get_api_catalog(project: str = "", tables: str = "") -> str:
    """查询 API 目录: 按表名 (逗号分隔) 或项目过滤, 返回 API 及其读写表。"""
    data = _load_api_table_map()
    if not data:
        return "ERROR: api_table_map.json 不存在 (静态分析未运行)"
    table_filter = {t.strip() for t in tables.split(",") if t.strip()}
    out: list[str] = []
    projects = [project] if project else list(data.keys())
    for p in projects:
        apis = data.get(p, {})
        for api, info in apis.items():
            tbls = info.get("tables_rw", [])
            if table_filter and not (set(tbls) & table_filter):
                continue
            line = f"{p} | {api} | 表: {','.join(tbls) if tbls else '(未知)'}"
            out.append(line)
    if not out:
        msg = f"无匹配 API (project={project or '全部'}, tables={tables or '全部'})"
        # 提示: 列出该表存在与否
        if table_filter:
            all_tables = {t for apis in data.values() for info in apis.values() for t in info.get('tables_rw', [])}
            msg += f"\n已知表: {sorted(all_tables)[:30]}"
        return msg
    return _truncate(f"共 {len(out)} 个 API:\n" + "\n".join(out[:80]))


def tool_query_db_readonly_guard(sql: str) -> str:
    """B 方案专用: 只读 DB 查询 (原有 query_db, 写操作在 B 方案下永久禁用)。"""
    return tool_query_db(sql)


# ═══════════════════════════════════════════════════════════════════════════════
# 工具 schema — B 方案: 无 execute_sql, 数据变更只能通过业务 API
# ═══════════════════════════════════════════════════════════════════════════════

TOOLS_SCHEMA = [
    {"name": "read_source", "description": "读取项目源码文件片段, 理解 sink 逻辑/前置条件/数据流。",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "相对 examples/ 的路径, 如 RuoYi-Cloud-Plus/ruoyi-modules/ruoyi-system/src/main/java/.../SysMenuServiceImpl.java"},
         "start": {"type": "integer", "description": "起始行(1-based)"},
         "end": {"type": "integer", "description": "结束行(含)"}},
         "required": ["path", "start", "end"]}},
    {"name": "search_code", "description": "在源码中正则搜索 (grep -E), 用于找 mapper XML/常量/表字段/其他引用。",
     "input_schema": {"type": "object", "properties": {
         "pattern": {"type": "string", "description": "正则表达式"},
         "path_prefix": {"type": "string", "description": "可选, 限定在 examples/ 下某子目录, 如 RuoYi-Cloud-Plus"}},
         "required": ["pattern"]}},
    {"name": "query_db", "description": "只读 SQL (SELECT/SHOW/DESC), 查表结构/现有数据。注意: 只读, 无法写入。",
     "input_schema": {"type": "object", "properties": {
         "sql": {"type": "string"}}, "required": ["sql"]}},
    {"name": "redis_cmd", "description": "Redis 只读/写命令 (预置缓存状态), 如 'GET key' 或 'SET key val'。",
     "input_schema": {"type": "object", "properties": {
         "cmd_str": {"type": "string", "description": "空格分隔, 如 'GET foo' 或 'SET foo bar EX 300'"}}, "required": ["cmd_str"]}},
    {"name": "read_logs", "description": "读取目标服务容器日志末尾, 获取执行偏差反馈 (走到哪个分支/异常信息)。",
     "input_schema": {"type": "object", "properties": {
         "container": {"type": "string", "description": "容器名"},
         "tail": {"type": "integer", "description": "末尾行数, 建议 60"}}, "required": ["container", "tail"]}},
    {"name": "send_http", "description": "发送 HTTP 请求 (最终 fuzz 请求或前置 API 调用)。",
     "input_schema": {"type": "object", "properties": {
         "method": {"type": "string"},
         "url": {"type": "string"},
         "headers_json": {"type": "string", "description": "JSON 对象字符串, 如 {\"Content-Type\":\"application/json\"}"},
         "body": {"type": "string", "description": "请求体, 可为空字符串"}}, "required": ["method", "url"]}},
    {"name": "docker_ps", "description": "列出运行中的 Docker 容器 (名称/状态/镜像), 用于确定目标服务的容器名。",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_api_catalog", "description": "查询静态分析生成的 API 目录: 每个 HTTP API 会读写哪些数据库表。"
     "用于找出'要创建/修改某表的数据, 应调用哪个业务 API'。",
     "input_schema": {"type": "object", "properties": {
         "project": {"type": "string", "description": "项目名, 如 RuoYi-Cloud-Plus; 留空查全部"},
         "tables": {"type": "string", "description": "逗号分隔的表名, 如 'sys_menu,sys_user'; 留空查全部"}},
         "required": []}},
]

TOOL_IMPL = {
    "read_source": lambda args: tool_read_source(args["path"], args["start"], args["end"]),
    "search_code": lambda args: tool_search_code(args["pattern"], args.get("path_prefix", "")),
    "query_db": lambda args: tool_query_db(args["sql"]),
    "redis_cmd": lambda args: tool_redis_cmd(args["cmd_str"]),
    "read_logs": lambda args: tool_read_logs(args["container"], args["tail"]),
    "send_http": lambda args: tool_send_http(args["method"], args["url"], args.get("headers_json", ""), args.get("body", "")),
    "docker_ps": lambda args: tool_docker(),
    "get_api_catalog": lambda args: tool_get_api_catalog(args.get("project", ""), args.get("tables", "")),
}


# ═══════════════════════════════════════════════════════════════════════════════
# Agent 循环 — OpenAI chat completions + tool use (兼容 paratera 网关)
# ═══════════════════════════════════════════════════════════════════════════════

def _to_openai_tools(schema: list[dict]) -> list[dict]:
    """Claude tool schema → OpenAI function 格式。"""
    return [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                              "parameters": t["input_schema"]}} for t in schema]


OPENAI_TOOLS = _to_openai_tools(TOOLS_SCHEMA)


class SinkAgent:
    def __init__(self, verbose: bool = False, max_tool_calls: int = 80):
        self.base_url = os.environ["LLM_BASE_URL"].rstrip("/")
        self.api_key = os.environ["LLM_API_KEY"]
        self.model = os.environ["LLM_MODEL"]
        self.verbose = verbose
        self.max_tool_calls = max_tool_calls
        self.messages: list[dict] = []
        self.tool_call_count = 0
        self.tool_log: list[dict] = []
        self.max_tool_calls = max_tool_calls

    def _api(self, payload: dict) -> dict:
        resp = requests.post(
            f"{self.base_url}/chat/completions",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
            json=payload, timeout=300,
        )
        resp.raise_for_status()
        return resp.json()

    def turn(self) -> tuple[bool, str]:
        """执行一轮 agent 循环。返回 (sink_reached, 终止原因)。"""
        payload = {
            "model": self.model,
            "max_tokens": 8192,
            "messages": self.messages,
            "tools": OPENAI_TOOLS,
            "tool_choice": "auto",
        }
        data = self._api(payload)

        while data["choices"][0].get("finish_reason") == "tool_calls":
            msg = data["choices"][0]["message"]
            self.messages.append(msg)

            tool_results = []
            for tc in msg.get("tool_calls", []):
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                if self.tool_call_count >= self.max_tool_calls:
                    tool_results.append({"role": "tool", "tool_call_id": tc["id"],
                                         "content": "BUDGET EXCEEDED: 工具调用预算已用尽, 请立即总结当前证据并给出结论。"})
                    continue
                self.tool_call_count += 1
                if self.verbose:
                    brief = {k: (v[:80] + "..." if isinstance(v, str) and len(v) > 80 else v)
                             for k, v in args.items()}
                    print(f"    🔧 [{self.tool_call_count}] {name} {brief}")
                try:
                    result = TOOL_IMPL[name](args)
                except Exception as e:
                    result = f"TOOL ERROR: {type(e).__name__}: {e}"
                self.tool_log.append({"n": self.tool_call_count, "tool": name, "args": args, "result": result[:2000]})
                tool_results.append({"role": "tool", "tool_call_id": tc["id"], "content": result[:MAX_TOOL_RESULT_CHARS]})

            self.messages.extend(tool_results)
            if self.tool_call_count >= self.max_tool_calls:
                # 强制结束: 不再发起新一轮 API 请求
                return False, "budget_exceeded"
            data = self._api(payload)

        msg = data["choices"][0]["message"]
        text = (msg.get("content") or "").strip()
        self.messages.append(msg)

        # 命中判定不在 agent 自述, 由 harness 外部完成 (run 中 grep 日志)
        reached = "<<SINK_REACHED>>" in text
        return reached, text

    def run(self, task_desc: str, container: str = "", max_turns: int = 15,
            log_match: str = "") -> dict:
        system_prompt = SYSTEM_PROMPT.replace("{task}", task_desc)
        self.messages = [{"role": "user", "content": system_prompt}]
        self._container = container
        self._log_match = log_match
        # harness 基线: 记录运行开始时的 marker 命中数, 只统计新增命中
        self._baseline_hits = self._count_sink_hits()
        t0 = time.time()
        text = ""
        completed_turns = 0
        for turn_i in range(1, max_turns + 1):
            print(f"  ── turn {turn_i} ──")
            try:
                reached, text = self.turn()
            except Exception as e:
                print(f"  ⚠ API 异常: {e}")
                time.sleep(2)
                continue
            completed_turns = turn_i
            print(f"    💭 {text[:400]}")
            elapsed = time.time() - t0
            # harness 侧命中判定: 不管 agent 是否声称成功, 直接 grep 容器日志 (对比基线增量)
            if self._count_sink_hits() > self._baseline_hits:
                print(f"  ✅ harness 判定 sink 命中 (本次运行新增目标日志命中)! 耗时 {elapsed:.1f}s, 工具调用 {self.tool_call_count} 次")
                return {"reached": True, "turns": turn_i, "tool_calls": self.tool_call_count,
                        "elapsed": elapsed, "tool_log": self.tool_log, "final_text": text}
            if reached:
                print(f"  ⚠ agent 自称命中但 harness 日志检查未见新增标记, 继续观察...")
            if text == "budget_exceeded":
                break
        return {"reached": False, "turns": completed_turns, "tool_calls": self.tool_call_count,
                "elapsed": time.time() - t0, "tool_log": self.tool_log, "final_text": text}

    def _count_sink_hits(self) -> int:
        """统计目标 sink 日志命中次数 (整个日志, 含历史)。

        默认仍按 marker 统计，兼容可控字符串参数的旧任务；如果任务提供
        ``log_match``，则必须命中目标日志前缀。这样不会把通用的请求前置日志
        (例如 ApiAccessLogInterceptor) 误判为 sink 命中。
        """
        container = getattr(self, "_container", "")
        if not container:
            return 0
        if container.endswith(".log"):
            try:
                with open(container) as f:
                    text = f.read()
            except OSError:
                return 0
        else:
            r = subprocess.run(["docker", "logs", container],
                               capture_output=True, text=True, timeout=60)
            text = r.stdout + r.stderr
        log_match = getattr(self, "_log_match", "")
        if log_match:
            return sum(1 for line in text.splitlines() if log_match in line)
        return text.count("SINK_REACHED")

SYSTEM_PROMPT = """你是一个 Java 微服务安全测试 agent, 目标是通过 HTTP 请求命中一条指定的日志 sink。

## 任务
{task}

## 工作流程 (推荐但不强制)
1. read_source 读 sink 所在方法源码, 理解条件分支和前置依赖
2. search_code 查 mapper XML / 常量 / 相关类, 补全信息 (如表名、字段、枚举值)
3. query_db 只读查询表结构和现有数据, 诊断缺什么前置数据/状态
4. 若需要创建/修改数据: 用 get_api_catalog 查"哪些业务 API 能写这张表", 然后调用该 API 完成前置 (你没有直接写数据库的权限)
5. send_http 发送触发请求 (注意: 接口可能需要登录 token, 可先调登录接口拿 token)
6. read_logs 查看服务日志, 判断: 命中了 sink? 还是走了别的分支? 还是报错?
7. 未命中时根据日志偏差修正参数/前置条件, 重试

## 硬性要求
- 最终必须通过 HTTP 请求触发
- 你没有数据库写权限 (INSERT/UPDATE/DELETE 被禁止), 所有数据变更必须通过业务 API 完成
- 你的请求参数中必须带攻击标记字符串 "<<SINK_REACHED>>", 放在会打印进日志的参数值里
- 无法确定目标服务容器名时, 用 docker_ps 查看

## 注意
- 攻击标记可能被应用的 XSS 过滤器部分剥除 (如 << >> 被剥), 若日志中标记变形, 换一个不会被过滤的字段注入
- 珍惜工具调用预算, 每轮多做几件事 (一次分析+一次请求+一次日志检查可以并行推理)
"""


# ═══════════════════════════════════════════════════════════════════════════════
# Sink 任务定义 — 通用模板: 只需 4 个字段, 换 sink 只改这里
# ═══════════════════════════════════════════════════════════════════════════════

SINK_TASKS = {
    "ruoyi_plus_menu_same_level": {
        # sink 描述: 类/方法/行号/日志模板/源码路径 — 从 sink JSON 自动生成即可
        "sink": "SysMenuServiceImpl.checkRouteConfigUnique 中的 "
                "log.warn(\"[同级路由冲突] 同级下已存在相同路由路径 '{}'，冲突菜单：{}\", dbPath, menuName) "
                "(约 L386, 源码 RuoYi-Cloud-Plus/ruoyi-modules/ruoyi-system/src/main/java/org/dromara/system/service/impl/SysMenuServiceImpl.java)",
        # API 入口: 从 pipeline 静态分析结果自动生成
        "api": "POST http://localhost:9201/menu (新增菜单, JSON body)",
        # 运行环境事实: 容器名/服务地址 — 部署清单自动生成
        "env": "目标服务容器: trace-real-ruoyi-cloud-plus-ruoyi-system (日志在此)。"
               "登录: POST http://localhost:8080/login, body {\"clientId\":\"e5cd7e4891bf95d1d19206ce24a7b32e\","
               "\"grantType\":\"password\",\"tenantId\":\"000000\",\"username\":\"admin\",\"password\":\"admin123\"}, "
               "取 data.access_token, 后续请求带 Authorization: Bearer <token>。",
        # 提示级别: none=纯黑盒对照 / hint=只给 sink+api+env (标准模式) / full=额外给前置提示
        "hint_level": "hint",
        "container": "trace-real-ruoyi-cloud-plus-ruoyi-system",
    },
    "yudao_router_orphan": {
        "sink": "AuthConvert.buildRouterTree 中的 "
                "log.error(\"[buildRouterTree][resource({}) 找不到父资源({})]\", childNode.getId(), childNode.getParentId()) "
                "(约 L68, 源码 yudao-cloud/yudao-module-system/yudao-module-system-server/src/main/java/cn/iocoder/yudao/module/system/convert/auth/AuthConvert.java)。"
                "触发条件: 获取当前用户权限信息时，构建菜单树若某菜单找不到父资源则触发此日志。",
        "api": "GET http://localhost:9214/admin-api/system/auth/get-permission-info (需登录, header: Authorization: Bearer <token>, tenant-id: 1)。"
               "登录: POST http://localhost:9214/admin-api/system/auth/login, header tenant-id: 1, body {\"username\":\"admin\",\"password\":\"admin123\"}, 取 data.accessToken。",
        "env": "目标服务容器: trace-real-yudao-cloud-yudao-module-system-server (日志在此)。"
               "数据库: docker 容器 trace-real-mysql, 库名 ruoyi-vue-pro, 菜单表 system_menu (字段: id/name/parent_id/status/deleted/type), 角色菜单关联表 system_role_menu。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-yudao-module-system-server",
        "log_match": "[buildRouterTree][resource(",
    },
    "yudao_system_notify_template_disabled": {
        "sink": "NotifySendServiceImpl.sendSingleNotify 中的 "
                "log.info(\"[sendSingleNotify][模版({})已经关闭，无法给用户({}/{})发送]\", templateCode, userId, userType) "
                "(约 L50, 源码 yudao-cloud/yudao-module-system/yudao-module-system-server/src/main/java/cn/iocoder/yudao/module/system/service/notify/NotifySendServiceImpl.java)。"
                "触发条件: 发送站内信时指定的模板处于关闭状态 (status=1)。",
        "api": "POST http://localhost:9214/admin-api/system/notify-template/send-notify (需登录, header: Authorization: Bearer <token>, tenant-id: 1, body: {\"userId\": 1, \"userType\": 1, \"templateCode\": \"TEST_DISABLED\", \"templateParams\": {}})。"
               "登录: POST http://localhost:9214/admin-api/system/auth/login, header tenant-id: 1, body {\"username\":\"admin\",\"password\":\"admin123\"}, 取 data.accessToken。",
        "env": "目标服务容器: trace-real-yudao-cloud-yudao-module-system-server (日志在此, 端口 9214)。"
               "数据库: docker 容器 trace-real-mysql, 库名 ruoyi-vue-pro, 站内信模板表 system_notify_template (字段: id/name/code/status/content/type)。已预置关闭状态模板 TEST_DISABLED (status=1)。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-yudao-module-system-server",
        "log_match": "[sendSingleNotify][模版(",
    },
    "yudao_system_dept_data_permission": {
        "sink": "PermissionServiceImpl.getDeptDataPermission 中的 "
                "log.error(\"[getDeptDataPermission][LoginUser({}) role({}) 无法处理]\", userId, toJsonString(result)) "
                "(约 L332, 源码 yudao-cloud/yudao-module-system/yudao-module-system-server/src/main/java/cn/iocoder/yudao/module/system/service/permission/PermissionServiceImpl.java)。"
                "触发条件: 查询用户部门数据权限时，若用户关联的角色的数据范围 data_scope 无法处理 (例如未预设范围值 99) 则触发此日志。",
        "api": "GET http://localhost:9214/rpc-api/system/permission/get-dept-data-permission?userId=104 (RPC 接口, 无需用户鉴权)。",
        "env": "目标服务容器: trace-real-yudao-cloud-yudao-module-system-server (日志在此, 端口 9214)。"
               "数据库: docker 容器 trace-real-mysql, 库名 ruoyi-vue-pro, 角色表 system_role (字段: id/name/code/data_scope), 用户角色关联表 system_user_role。已预置用户 104 关联非法范围角色 (data_scope=99)。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-yudao-module-system-server",
        "log_match": "[getDeptDataPermission][LoginUser(",
    },
    "yudao_system_user_dept_parse_function_not_found": {
        "sink": "DeptParseFunction.apply 中的 "
                "log.warn(\"[apply][获取部门{{}}为空\", value) "
                "(约 L41, 源码 yudao-cloud/yudao-module-system/yudao-module-system-server/src/main/java/cn/iocoder/yudao/module/system/framework/operatelog/core/DeptParseFunction.java)。"
                "触发条件: 更新用户信息时触发操作日志 DIFF 计算，当被比较的用户原部门编号在系统中不存在 (例如 9999) 时触发此警告日志。",
        "api": "PUT http://localhost:9214/admin-api/system/user/update (需登录, header: Authorization: Bearer <token>, tenant-id: 1, body: {\"id\": 103, \"username\": \"yuanma\", \"nickname\": \"源码\", \"deptId\": 103, \"postIds\": []})。"
               "登录: POST http://localhost:9214/admin-api/system/auth/login, header tenant-id: 1, body {\"username\":\"admin\",\"password\":\"admin123\"}, 取 data.accessToken。",
        "env": "目标服务容器: trace-real-yudao-cloud-yudao-module-system-server (日志在此, 端口 9214)。"
               "数据库: docker 容器 trace-real-mysql, 库名 ruoyi-vue-pro, 用户表 system_users (字段: id/username/nickname/dept_id/post_ids)。已预置用户 103 原部门 dept_id 为不存在的编号 (9999)。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-yudao-module-system-server",
        "log_match": "[apply][获取部门{",
    },
    "yudao_system_user_post_parse_function_not_found": {
        "sink": "PostParseFunction.apply 中的 "
                "log.warn(\"[apply][获取岗位{{}}为空\", value) "
                "(约 L41, 源码 yudao-cloud/yudao-module-system/yudao-module-system-server/src/main/java/cn/iocoder/yudao/module/system/framework/operatelog/core/PostParseFunction.java)。"
                "触发条件: 更新用户信息时触发操作日志 DIFF 计算，当被比较的用户原岗位编号在系统中不存在 (例如 9999) 时触发此警告日志。",
        "api": "PUT http://localhost:9214/admin-api/system/user/update (需登录, header: Authorization: Bearer <token>, tenant-id: 1, body: {\"id\": 103, \"username\": \"yuanma\", \"nickname\": \"源码\", \"deptId\": 103, \"postIds\": []})。"
               "登录: POST http://localhost:9214/admin-api/system/auth/login, header tenant-id: 1, body {\"username\":\"admin\",\"password\":\"admin123\"}, 取 data.accessToken。",
        "env": "目标服务容器: trace-real-yudao-cloud-yudao-module-system-server (日志在此, 端口 9214)。"
               "数据库: docker 容器 trace-real-mysql, 库名 ruoyi-vue-pro, 用户表 system_users (字段: id/username/nickname/dept_id/post_ids)。已预置用户 103 原岗位 post_ids 为包含不存在的编号 ([9999])。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-yudao-module-system-server",
        "log_match": "[apply][获取岗位{",
    },
    # ── ruoyi-plus 批量 (db_state 目标池 10 条) ──
    "ruoyi_plus_menu_root": {
        "sink": "SysMenuServiceImpl.checkRouteConfigUnique 中的 "
                "log.warn(\"[根目录路由冲突] 根目录下路由 '{}' 必须唯一，已被菜单 '{}' 占用\", path, sysMenu.getMenuName()) "
                "(约 L391, 源码 RuoYi-Cloud-Plus/ruoyi-modules/ruoyi-system/src/main/java/org/dromara/system/service/impl/SysMenuServiceImpl.java)",
        "api": "POST http://localhost:9201/menu (新增菜单, JSON body)",
        "env": "目标服务容器: trace-real-ruoyi-cloud-plus-ruoyi-system (日志在此)。"
               "登录: POST http://localhost:8080/login, body {\"clientId\":\"e5cd7e4891bf95d1d19206ce24a7b32e\","
               "\"grantType\":\"password\",\"tenantId\":\"000000\",\"username\":\"admin\",\"password\":\"admin123\"}, "
               "取 data.access_token, 后续请求带 Authorization: Bearer <token>。",
        "hint_level": "hint",
        "container": "trace-real-ruoyi-cloud-plus-ruoyi-system",
    },
    "ruoyi_plus_menu_route_name": {
        "sink": "SysMenuServiceImpl.checkRouteConfigUnique 中的 "
                "log.warn(\"[路由名称冲突] 路由名称 '{}' 需全局唯一，已被菜单 '{}' 使用\", routeName, sysMenu.getMenuName()) "
                "(约 L395, 源码 RuoYi-Cloud-Plus/ruoyi-modules/ruoyi-system/src/main/java/org/dromara/system/service/impl/SysMenuServiceImpl.java)",
        "api": "POST http://localhost:9201/menu (新增菜单, JSON body)",
        "env": "目标服务容器: trace-real-ruoyi-cloud-plus-ruoyi-system (日志在此)。"
               "登录: POST http://localhost:8080/login, body {\"clientId\":\"e5cd7e4891bf95d1d19206ce24a7b32e\","
               "\"grantType\":\"password\",\"tenantId\":\"000000\",\"username\":\"admin\",\"password\":\"admin123\"}, "
               "取 data.access_token, 后续请求带 Authorization: Bearer <token>。",
        "hint_level": "hint",
        "container": "trace-real-ruoyi-cloud-plus-ruoyi-system",
    },
    "ruoyi_plus_mybatis_duplicate": {
        "sink": "MybatisExceptionHandler 中的 "
                "log.error(\"请求地址'{}',数据库中已存在记录'{}'\", request.getRequestURI(), e.getCause().getMessage()) "
                "(约 L29, 源码 RuoYi-Cloud-Plus/ruoyi-common/ruoyi-common-web/src/main/java/org/dromara/common/web/handler/MybatisExceptionHandler.java)。"
                "触发条件: 任意带唯一键约束的表插入重复记录 (org.springframework.dao.DuplicateKeyException)。",
        "api": "多个写 API 可触发, 如 POST http://localhost:9201/menu (需先存在同唯一键记录)",
        "env": "目标服务容器: trace-real-ruoyi-cloud-plus-ruoyi-system (日志在此)。"
               "登录: POST http://localhost:8080/login, body {\"clientId\":\"e5cd7e4891bf95d1d19206ce24a7b32e\","
               "\"grantType\":\"password\",\"tenantId\":\"000000\",\"username\":\"admin\",\"password\":\"admin123\"}, "
               "取 data.access_token, 后续请求带 Authorization: Bearer <token>。",
        "hint_level": "hint",
        "container": "trace-real-ruoyi-cloud-plus-ruoyi-system",
    },
    # ── ruoyi-plus 工作流模块 (6 条, 目标服务 ruoyi-workflow, 端口 9205) ──
    # 前置数据 (已通过 API 预置): 流程定义 leave_test 已导入并发布 (id=2098658892962639874),
    # 已有一条走完的流程实例 (flow_status=finish) 和 3 条 flow_his_task。
    # agent 无需再导入定义; 但 agent 需自己发现上述状态 (query_db 只读 + read_logs)。
    "ruoyi_plus_wf_def_inuse": {
        "sink": "FlwDefinitionServiceImpl.removeDef 中的 "
                "log.info(\"流程定义【{}】已被使用不可被删除！\", join) "
                "(L192, 源码 RuoYi-Cloud-Plus/ruoyi-modules/ruoyi-workflow/src/main/java/org/dromara/workflow/service/impl/FlwDefinitionServiceImpl.java)。"
                "触发条件: 删除一个已被流程实例/历史任务使用过的流程定义 (flow_his_task 中存在 definitionId 引用)。",
        "api": "DELETE http://localhost:9205/definition/{ids} (workflow 服务直连 9205, 非 8080 网关)",
        "env": "目标服务容器: trace-real-ruoyi-cloud-plus-ruoyi-workflow (日志在此)。"
               "登录: POST http://localhost:8080/login, body {\"clientId\":\"e5cd7e4891bf95d1d19206ce24a7b32e\","
               "\"grantType\":\"password\",\"tenantId\":\"000000\",\"username\":\"admin\",\"password\":\"admin123\"}, "
               "取 data.access_token, 请求头 Authorization: Bearer <token> + clientid。"
               "workflow 数据库: ry-workflow (flow_definition/flow_instance/flow_his_task 表)。",
        "hint_level": "hint",
        "container": "trace-real-ruoyi-cloud-plus-ruoyi-workflow",
    },
    "ruoyi_plus_wf_def_sync": {
        "sink": "FlwDefinitionServiceImpl.syncDef 中的两条日志: "
                "log.info(\"同步流程定义【{}】成功！\", definition.getFlowCode()) (L243) 与 "
                "log.info(\"同步流程定义【{}】失败！\", definition.getFlowCode()) (L240)。"
                "(源码 RuoYi-Cloud-Plus/ruoyi-modules/ruoyi-workflow/src/main/java/org/dromara/workflow/service/impl/FlwDefinitionServiceImpl.java)。"
                "触发条件: 创建租户 (system 服务) 时通过 dubbo 远程调用 workflow.syncDef(tenantId), 把 000000 租户的流程定义复制给新租户。",
        "api": "POST http://localhost:9201/tenant (system 服务, JSON body 需 contactUserName/contactPhone/companyName/username/password/packageId)。"
               "注意: packageId 必须是已存在的租户套餐 (POST /tenant/package 可创建)",
        "env": "目标服务容器: trace-real-ruoyi-cloud-plus-ruoyi-workflow (日志在此, dubbo 异步调用链)。"
               "登录: POST http://localhost:8080/login (同上), 取 data.access_token。"
               "前置: sys_tenant_package 需至少一条套餐记录 (可先 POST http://localhost:9201/tenant/package 创建)。",
        "hint_level": "hint",
        "container": "trace-real-ruoyi-cloud-plus-ruoyi-workflow",
    },
    "ruoyi_plus_wf_inst_var": {
        "sink": "FlwInstanceServiceImpl.updateVariable 中的 "
                "log.error(\"变量不存在: {}\", bo.getKey()) "
                "(L423, 源码 RuoYi-Cloud-Plus/ruoyi-modules/ruoyi-workflow/src/main/java/org/dromara/workflow/service/impl/FlwInstanceServiceImpl.java)。"
                "触发条件: 对一个已存在的流程实例 updateVariable, key 不在该实例的 variableMap 中。",
        "api": "PUT http://localhost:9205/instance/updateVariable (JSON body: {\"instanceId\":<id>,\"key\":\"...\",\"value\":\"...\"})",
        "env": "目标服务容器: trace-real-ruoyi-cloud-plus-ruoyi-workflow (日志在此)。"
               "登录: POST http://localhost:8080/login (同上), 取 data.access_token。"
               "已存在流程实例: ry-workflow.flow_instance 表 (id 可查), instanceId 用该表主键。",
        "hint_level": "hint",
        "container": "trace-real-ruoyi-cloud-plus-ruoyi-workflow",
    },
    "ruoyi_plus_wf_inst_orphan": {
        "sink": "FlwInstanceServiceImpl.processDeleteHandler 中的 "
                "log.warn(\"实例 ID: {} 对应的流程定义信息未找到，跳过删除事件触发。\", instance.getId()) "
                "(L269, 源码 RuoYi-Cloud-Plus/ruoyi-modules/ruoyi-workflow/src/main/java/org/dromara/workflow/service/impl/FlwInstanceServiceImpl.java)。"
                "触发条件: 删除流程实例时, 实例的 definitionId 在 flow_definition 中查不到 (孤儿实例)。"
                "注意: 正常路径下定义被 his_task 引用保护不可删 (L192), 可能需要先推理出构造孤儿实例的非常规顺序。",
        "api": "DELETE http://localhost:9205/instance/deleteByInstanceIds/{instanceIds}",
        "env": "目标服务容器: trace-real-ruoyi-cloud-plus-ruoyi-workflow (日志在此)。"
               "登录: POST http://localhost:8080/login (同上), 取 data.access_token。",
        "hint_level": "hint",
        "container": "trace-real-ruoyi-cloud-plus-ruoyi-workflow",
    },
    "ruoyi_plus_wf_listener_end": {
        "sink": "WorkflowGlobalListener.determineFlowStatus 中的 "
                "log.info(\"流程已结束，状态更新为: {}\", status) "
                "(L270, 源码 RuoYi-Cloud-Plus/ruoyi-modules/ruoyi-workflow/src/main/java/org/dromara/workflow/listener/WorkflowGlobalListener.java)。"
                "触发条件: 一个流程实例的所有任务办理完成 (最后一节点 completeTask 后 isTaskEnd 为真)。",
        "api": "workflow 模块的任务接口 (发起/办理任务), 直连 9205 端口",
        "env": "目标服务容器: trace-real-ruoyi-cloud-plus-ruoyi-workflow (日志在此)。"
               "登录: POST http://localhost:8080/login (同上), 取 data.access_token。"
               "环境业务基线: 已存在一个已发布的流程定义 (flow_definition 表 is_publish=1, flow_code 可查), "
               "其余业务数据为空。warm-flow 引擎的定义节点图格式属于引擎内部知识, 环境中已有合法定义可直接使用。",
        "hint_level": "hint",
        "container": "trace-real-ruoyi-cloud-plus-ruoyi-workflow",
    },
    # ── pig (pig-boot 单体 9999, 直连无网关) ──
    "pig_file_not_exist": {
        "sink": "SysFileServiceImpl.getFile 中的 "
                "log.warn(\"文件不存在: {}\", fileName) "
                "(L124, 源码 examples/pig/pig-upms/pig-upms-biz/src/main/java/com/pig4cloud/pig/admin/service/impl/SysFileServiceImpl.java)。"
                "触发条件: 请求下载一个 sys_file 表中不存在的文件名。",
        "api": "GET http://localhost:9999/admin/sys-file/oss/file?fileName=<不存在的文件名> (@Inner 免鉴权, 直接 GET 即可)",
        "env": "目标服务: pig-boot 单体运行在宿主机 9999 端口 (非容器), 日志文件: /tmp/pigboot.log — "
               "read_logs 的 container 参数直接传这个文件路径即可读取末尾日志。"
               "pig 无需登录即可访问该接口。数据库: docker 容器 trace-real-mysql 里的 pig 库 (sys_file 表)。",
        "hint_level": "hint",
        "container": "/tmp/pigboot.log",
    },
    "pig_sms_unregistered": {
        "sink": "SysMessageServiceImpl.sendSmsCode 中的 "
                "log.info(\"手机号未注册:{}\", mobile) "
                "(L305, 源码 examples/pig/pig-upms/pig-upms-biz/src/main/java/com/pig4cloud/pig/admin/service/impl/SysMessageServiceImpl.java)。"
                "触发条件: 请求发送短信验证码, registered=true 且手机号在 sys_user 表中不存在; "
                "该接口有算术验证码校验 (math 类型), 需先获取验证码图并读出算式答案。",
        "api": "GET http://localhost:9999/admin/sysMessage/send/smsCode (短信验证码发送接口)",
        "env": "目标服务: pig-boot 单体运行在宿主机 9999 端口, 日志文件: /tmp/pigboot.log。"
               "登录: POST http://localhost:9999/admin/oauth2/token, Basic 认证 test:test, "
               "form: grant_type=password&scope=server&username=admin&password=<明文密码即可, 工具层自动加密>。"
               "redis: docker exec trace-real-redis redis-cli (redis_cmd 工具, 密码与库号需自行从配置/尝试获得)。"
               "数据库: trace-real-mysql 的 pig 库 (sys_user 表查已注册手机号)。",
        "hint_level": "hint",
        "container": "/tmp/pigboot.log",
    },
    "pig_change_password_wrong": {
        "sink": "SysUserServiceImpl.changePassword 中的 "
                "log.info(\"原密码错误，修改个人信息失败:{}\", userDto.getUsername()) "
                "(L586, 源码 examples/pig/pig-upms/pig-upms-biz/src/main/java/com/pig4cloud/pig/admin/service/impl/SysUserServiceImpl.java)。"
                "触发条件: 登录后修改个人密码, 但 password 字段 (原密码) 与数据库中 BCrypt 哈希不匹配。",
        "api": "PUT http://localhost:9999/admin/user/personal/password (body: {\"password\":\"错误的旧密码\",\"newpassword1\":\"x\",\"newpassword2\":\"x\"})",
        "env": "目标服务: pig-boot 单体运行在宿主机 9999 端口, 日志文件: /tmp/pigboot.log。"
               "登录: POST http://localhost:9999/admin/oauth2/token, Basic 认证 test:test, "
               "form: grant_type=password&scope=server&username=admin&password=<明文密码即可, 工具层自动加密>, 取 access_token。"
               "当前用户是 admin (userId=1)。",
        "hint_level": "hint",
        "container": "/tmp/pigboot.log",
    },
    # ── pig db_state 第二批 (2026-09-14) ──
    "pig_sms_login_not_exist": {
        "sink": "SmsLoginHandler.info 中的 "
                "log.info(\"手机号 不存在用户:{}\", identify) "
                "(L69, 源码 examples/pig/pig-upms/pig-upms-biz/src/main/java/com/pig4cloud/pig/admin/handler/SmsLoginHandler.java)。"
                "触发条件: 走短信验证码登录 (oauth2 密码模式), 手机号在 sys_user 表中不存在。",
        "api": "POST http://localhost:9999/admin/oauth2/token (grant_type=mobile 相关流程, Basic 认证与密码加密同其他任务)",
        "env": "目标服务: pig-boot 单体 9999 端口, 日志文件: /tmp/pigboot.log。"
               "登录端点: POST http://localhost:9999/admin/oauth2/token, Basic 认证 test:test。"
               "数据库: trace-real-mysql 的 pig 库 (sys_user 表查已注册手机号)。"
               "redis: docker exec trace-real-redis redis-cli (密码/库号自行获取)。"
               "注意: 短信登录可能有验证码校验, 校验逻辑与答案存储方式需自行从源码推理。",
        "hint_level": "hint",
        "container": "/tmp/pigboot.log",
    },
    "pig_opaque_token_locked": {
        "sink": "PigCustomOpaqueTokenIntrospector.introspect 中的 "
                "log.warn(\"用户账号 {} 已被锁定，拒绝访问\", userDetails.getUsername()) "
                "(L95, 源码 examples/pig/pig-common/pig-common-security/src/main/java/com/pig4cloud/pig/common/security/component/PigCustomOpaqueTokenIntrospector.java)。"
                "触发条件: 持有 token 访问受保护资源时, token 内省检查发现该用户 lockFlag 为锁定态。",
        "api": "锁用户: PUT http://localhost:9999/admin/user/lock/{username}; 之后携带 token 访问任意受保护资源触发内省",
        "env": "目标服务: pig-boot 单体 9999 端口, 日志文件: /tmp/pigboot.log。"
               "登录: POST http://localhost:9999/admin/oauth2/token, Basic test:test, "
               "form: grant_type=password&scope=server&username=admin&password=<工具层自动加密>。"
               "提示: 当前用户是 admin; 想想 introspect 在什么时候被调用、以及先锁谁再访问才能保证链路完整。",
        "hint_level": "hint",
        "container": "/tmp/pigboot.log",
    },
    "pig_apikey_token_locked": {
        "sink": "PigCustomOpaqueTokenIntrospector.handleApiKeyToken 中的 "
                "log.warn(\"用户账号 {} 已被锁定，拒绝 API Key 访问\", pigUser.getUsername()) "
                "(L163, 源码 examples/pig/pig-common/pig-common-security/src/main/java/com/pig4cloud/pig/common/security/component/PigCustomOpaqueTokenIntrospector.java)。"
                "触发条件: 携带以 sk- 开头的 API Key 访问受保护资源, 该 API Key 对应的用户处于锁定态 (lockFlag 为锁定态)。",
        "api": "POST http://localhost:9999/admin/api-key 创建 API Key (需携带登录 token 与当前用户密码); "
               "锁用户: PUT http://localhost:9999/admin/user/lock/{username}; 之后携带 Authorization: Bearer <sk-xxx> 访问任意受保护资源触发内省。",
        "env": "目标服务: pig-boot 单体 9999 端口, 日志文件: /tmp/pigboot.log (宿主机文件)。"
               "登录端点: POST http://localhost:9999/admin/oauth2/token, Basic test:test, "
               "form: grant_type=password&scope=server&username=admin&password=<工具层自动加密>。"
               "数据库: trace-real-mysql 的 pig 库。",
        "hint_level": "hint",
        "container": "/tmp/pigboot.log",
        "log_match": "已被锁定，拒绝 API Key 访问",
    },
    "yudao_pay_channel_not_found": {
        "sink": "PayNotifyController.notifyOrder 中的 "
                "log.error(\"[notifyOrder][渠道编号({}) 找不到对应的支付客户端]\", channelId) "
                "(L75, 源码 examples/yudao-cloud/yudao-module-pay/yudao-module-pay-server/src/main/java/cn/iocoder/yudao/module/pay/controller/admin/notify/PayNotifyController.java)。"
                "触发条件: POST 支付回调接口时, channelId 在支付渠道配置中不存在。",
        "api": "POST http://localhost:9213/admin-api/pay/notify/order/{channelId} (支付回调入口, admin-api 前缀)",
        "env": "目标服务容器: trace-real-yudao-cloud-pay-server (日志在此, 端口 9213, pay-server 独立部署)。"
               "该接口标注 PermitAll/TenantIgnore, 可直接发送请求; 数据库为 ruoyi-vue-pro, 支付渠道表 pay_channel 已建空表。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-pay-server",
        # channelId 是 Long，marker 无法进入目标日志参数；按目标日志前缀
        # 判定，避免把 ApiAccessLogInterceptor 的请求参数日志算作命中。
        "log_match": "[notifyOrder][渠道编号(",
        # 已知静态可达性问题: PayChannelServiceImpl.getPayClient (L160-163) 在渠道
        # 不存在时经 validPayChannel 抛 CHANNEL_NOT_FOUND (L146-147)，永远不会返回
        # null；Controller L74 的 if (payClient == null) 分支 (含 L75 目标日志) 是
        # 防御性代码，当前版本静态不可达。此任务保留用于验证 harness 精确判定，
        # 预期 reached=False 为正确结果。
        "static_unreachable_note": "getPayClient 渠道缺失时抛异常不返回 null, L75 不可达",
    },
    # ── yudao pay 第二批 (2026-09-27, pay-server 9213) ──
    "yudao_pay_demo_order_duplicate_notify": {
        "sink": "PayDemoOrderServiceImpl.updateDemoOrderPaid 中的 "
                "log.warn(\"[updateDemoOrderPaid][order({}) 已支付，且支付单号相同({})，直接返回]\", order, payOrderId) "
                "(L126, 源码 yudao-cloud/yudao-module-pay/yudao-module-pay-server/src/main/java/cn/iocoder/yudao/module/pay/service/demo/PayDemoOrderServiceImpl.java)。"
                "触发条件: 回调更新示例订单时, 该订单 pay_status=已支付 且请求的 payOrderId 与库中记录一致 (重复回调幂等分支)。",
        "api": "POST http://localhost:9213/admin-api/pay/demo-order/update-paid "
               "(body: {\"merchantOrderId\":\"<订单id>\",\"payOrderId\":\"<支付单id>\"}, @PermitAll 免登录)。",
        "env": "目标服务容器: trace-real-yudao-cloud-pay-server (日志在此, 端口 9213)。"
               "数据库 ruoyi-vue-pro 的 pay_demo_order 表 (id/merchantOrderId 对应主键, pay_status/pay_order_id 字段); "
               "订单创建 API: POST /admin-api/pay/demo-order/create (需登录, 慎用, 直接查现有记录即可)。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-pay-server",
        # 日志参数是 order 对象与 payOrderId (数值), marker 放不进参数;
        # 用目标日志前缀精确判定, 不依赖 marker。
        "log_match": "[updateDemoOrderPaid][order(",
    },
    "yudao_pay_demo_order_not_found": {
        "sink": "PayDemoOrderServiceImpl.updateDemoOrderPaid 中的 "
                "log.error(\"[updateDemoOrderPaid][order({}) payOrder({}) 不存在订单，请进行处理！]\", id, payOrderId) "
                "(L119, 源码 yudao-cloud/yudao-module-pay/yudao-module-pay-server/src/main/java/cn/iocoder/yudao/module/pay/service/demo/PayDemoOrderServiceImpl.java)。"
                "触发条件: 回调更新示例订单时, 请求中的示例订单 id 在 pay_demo_order 表中不存在。",
        "api": "POST http://localhost:9213/admin-api/pay/demo-order/update-paid "
               "(body: {\"merchantOrderId\":\"999999\",\"payOrderId\":1}, @PermitAll 免登录)。",
        "env": "目标服务容器: trace-real-yudao-cloud-pay-server (日志在此, 端口 9213)。"
               "数据库 ruoyi-vue-pro 的 pay_demo_order 表。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-pay-server",
        "log_match": "PayDemoOrderServiceImpl:119",
    },
    "yudao_pay_demo_order_paid_mismatch": {
        "sink": "PayDemoOrderServiceImpl.updateDemoOrderPaid 中的 "
                "log.error(\"[updateDemoOrderPaid][order({}) 支付单不匹配({})，请进行处理！]\", order, payOrderId) "
                "(L130, 源码 yudao-cloud/yudao-module-pay/yudao-module-pay-server/src/main/java/cn/iocoder/yudao/module/pay/service/demo/PayDemoOrderServiceImpl.java)。"
                "触发条件: 回调更新示例订单时, 示例订单处于已支付状态(pay_status=1, pay_order_id=50001), 请求传入的 payOrderId 与库中记录不一致。",
        "api": "POST http://localhost:9213/admin-api/pay/demo-order/update-paid "
               "(body: {\"merchantOrderId\":\"1\",\"payOrderId\":999999}, @PermitAll 免登录)。",
        "env": "目标服务容器: trace-real-yudao-cloud-pay-server (日志在此, 端口 9213)。"
               "数据库 ruoyi-vue-pro 的 pay_demo_order 表 (已预置 pay_status=1, pay_order_id=50001 的订单 id=1)。"
               "库中已预置测试记录, 请直接向 pay-server 发送 update-paid 回调, 无需探索其他服务或从头创建订单。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-pay-server",
        "log_match": "PayDemoOrderServiceImpl:130",
    },
    "yudao_pay_demo_order_refund_mismatch": {
        "sink": "PayDemoOrderServiceImpl.updateDemoOrderRefunded 中的 "
                "log.error(\"[validateDemoOrderCanRefunded][order({}) 退款单不匹配({})，请进行处理！order 数据是：{}]\", id, payRefundId, toJsonString(order)) "
                "(L239, 源码 yudao-cloud/yudao-module-pay/yudao-module-pay-server/src/main/java/cn/iocoder/yudao/module/pay/service/demo/PayDemoOrderServiceImpl.java)。"
                "触发条件: 退款回调更新时, 订单记录中的退款单号(pay_refund_id=70001)与请求传入的 payRefundId(999999) 不一致。",
        "api": "POST http://localhost:9213/admin-api/pay/demo-order/update-refunded "
               "(body: {\"merchantOrderId\":\"2\",\"merchantRefundId\":\"2-refund\",\"payRefundId\":999999}, @PermitAll 免登录)。",
        "env": "目标服务容器: trace-real-yudao-cloud-pay-server (日志在此, 端口 9213)。"
               "数据库 ruoyi-vue-pro 的 pay_demo_order 表 (已预置 pay_refund_id=70001 的订单 id=2)。"
               "库中已预置测试记录, 请直接向 pay-server 发送 update-refunded 回调, 无需探索其他服务或从头创建订单。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-pay-server",
        "log_match": "PayDemoOrderServiceImpl:239",
    },
    "yudao_pay_demo_order_refund_price_mismatch": {
        "sink": "PayDemoOrderServiceImpl.updateDemoOrderRefunded 中的 "
                "log.error(\"[validateDemoOrderCanRefunded][order({}) payRefund({}) 退款金额不匹配，请进行处理！order 数据是：{}，payRefund 数据是：{}]\", id, payRefundId, toJsonString(order), toJsonString(payRefund)) "
                "(L255, 源码 yudao-cloud/yudao-module-pay/yudao-module-pay-server/src/main/java/cn/iocoder/yudao/module/pay/service/demo/PayDemoOrderServiceImpl.java)。"
                "触发条件: 退款回调更新时, 退款单金额(payRefund.refundPrice=200)与示例订单金额(order.price=100)不一致。",
        "api": "POST http://localhost:9213/admin-api/pay/demo-order/update-refunded "
               "(body: {\"merchantOrderId\":\"3\",\"merchantRefundId\":\"3-refund\",\"payRefundId\":601}, @PermitAll 免登录)。",
        "env": "目标服务容器: trace-real-yudao-cloud-pay-server (日志在此, 端口 9213)。"
               "数据库 ruoyi-vue-pro 的 pay_demo_order 表 (已预置 price=100, pay_refund_id=601 的订单 id=3) "
               "与 pay_refund 表 (已预置 status=10, refund_price=200 的退款单 id=601)。"
               "库中已预置测试记录, 请直接向 pay-server 发送 update-refunded 回调。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-pay-server",
        "log_match": "PayDemoOrderServiceImpl:255",
    },
    "yudao_pay_demo_order_refund_order_mismatch": {
        "sink": "PayDemoOrderServiceImpl.updateDemoOrderRefunded 中的 "
                "log.error(\"[validateDemoOrderCanRefunded][order({}) 退款单不匹配({})，请进行处理！payRefund 数据是：{}]\", id, payRefundId, toJsonString(payRefund)) "
                "(L261, 源码 yudao-cloud/yudao-module-pay/yudao-module-pay-server/src/main/java/cn/iocoder/yudao/module/pay/service/demo/PayDemoOrderServiceImpl.java)。"
                "触发条件: 退款回调更新时, 退款单中的商户退款单号(payRefund.merchantRefundId='999')与示例订单生成的退款单号('4-refund')不一致。",
        "api": "POST http://localhost:9213/admin-api/pay/demo-order/update-refunded "
               "(body: {\"merchantOrderId\":\"4\",\"merchantRefundId\":\"4-refund\",\"payRefundId\":602}, @PermitAll 免登录)。",
        "env": "目标服务容器: trace-real-yudao-cloud-pay-server (日志在此, 端口 9213)。"
               "数据库 ruoyi-vue-pro 的 pay_demo_order 表 (已预置 price=100, pay_refund_id=602 的订单 id=4) "
               "与 pay_refund 表 (已预置 status=10, refund_price=100, merchant_refund_id='999' 的退款单 id=602)。"
               "库中已预置测试记录, 请直接向 pay-server 发送 update-refunded 回调。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-pay-server",
        "log_match": "PayDemoOrderServiceImpl:261",
    },
    "yudao_pay_order_create_order_exists": {
        "sink": "PayOrderServiceImpl.createOrder 中的 "
                "log.warn(\"[createOrder][appId({}) merchantOrderId({}) 已经存在对应的支付单({})]\", order.getAppId(), order.getMerchantOrderId(), toJsonString(order)) "
                "(L124, 源码 yudao-cloud/yudao-module-pay/yudao-module-pay-server/src/main/java/cn/iocoder/yudao/module/pay/service/order/PayOrderServiceImpl.java)。"
                "触发条件: 创建支付单时, 传入的商户订单号在当前应用下已经存在支付单 (appId=1, merchantOrderId='200', 对应已有支付单 id=500)。",
        "api": "POST http://localhost:9213/rpc-api/pay/order/create "
               "(body: {\"appKey\":\"demo_app\",\"userIp\":\"127.0.0.1\",\"merchantOrderId\":\"200\",\"subject\":\"测试商品\",\"price\":100,\"expireTime\":\"2026-10-18T12:00:00\"}, RPC 免登录)。",
        "env": "目标服务容器: trace-real-yudao-cloud-pay-server (日志在此, 端口 9213)。"
               "数据库 ruoyi-vue-pro 的 pay_order 表 (已存在 id=500, app_id=1, merchant_order_id='200') 与 pay_app 表 (已预置 app_key='demo_app')。"
               "请直接向 pay-server 发送 POST /rpc-api/pay/order/create 请求, 无需登录认证。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-pay-server",
        "log_match": "PayOrderServiceImpl:124",
    },
    "yudao_pay_wallet_client_order_status_invalid": {
        "sink": "WalletPayClient.doGetOrder 中的 "
                "log.error(\"[doGetOrder] 支付单 {} 的状态不正确\", outTradeNo) "
                "(L123, 源码 yudao-cloud/yudao-module-pay/yudao-module-pay-server/src/main/java/cn/iocoder/yudao/module/pay/framework/pay/core/client/impl/wallet/WalletPayClient.java)。"
                "触发条件: 获取订单详情并请求同步(sync=true)时, 支付拓展单所关联的支付单处于未支付(status=0)状态, 触发状态不正确分支打印错误日志。",
        "api": "GET http://localhost:9213/app-api/pay/order/get?id=500&sync=true (免登录)。",
        "env": "目标服务容器: trace-real-yudao-cloud-pay-server (日志在此, 端口 9213)。"
               "数据库 ruoyi-vue-pro 的 pay_order 表 (已存在 id=500, status=0), pay_channel 表 (已存在 id=1, code='wallet'), pay_order_extension 表 (已存在 order_id=500, channel_id=1)。"
               "请直接向 pay-server 发送 GET /app-api/pay/order/get?id=500&sync=true 请求, 无需登录认证。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-pay-server",
        "log_match": "WalletPayClient:123",
    },
    "yudao_pay_wallet_client_transfer_status_invalid": {
        "sink": "WalletPayClient.doGetTransfer 中的 "
                "log.error(\"[doGetTransfer] 转账单 {} 的状态不正确\", outTradeNo) "
                "(L247, 源码 yudao-cloud/yudao-module-pay/yudao-module-pay-server/src/main/java/cn/iocoder/yudao/module/pay/framework/pay/core/client/impl/wallet/WalletPayClient.java)。"
                "触发条件: 同步转账单(GET /app-api/pay/transfer/sync?id=...)时, 钱包渠道对应的转账单处于非正常状态(status=99), 触发状态不正确分支打印错误日志。",
        "api": "GET http://localhost:9213/app-api/pay/transfer/sync?id=705 (免登录)。",
        "env": "目标服务容器: trace-real-yudao-cloud-pay-server (日志在此, 端口 9213)。"
               "数据库 ruoyi-vue-pro 的 pay_transfer 表 (已预置 id=705, channel_id=1, channel_code='wallet', status=99)。"
               "请直接向 pay-server 发送 GET /app-api/pay/transfer/sync?id=705 请求, 无需登录认证。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-pay-server",
        "log_match": "[doGetTransfer] 转账单",
    },
    "yudao_pay_transfer_sync_closed": {
        "sink": "PayTransferServiceImpl.notifyTransferClosed 中的 "
                "log.info(\"[notifyTransferClosed][transfer({}) 已经是关闭状态，无需更新]\", transfer.getId()) "
                "(L246, 源码 yudao-cloud/yudao-module-pay/yudao-module-pay-server/src/main/java/cn/iocoder/yudao/module/pay/service/transfer/PayTransferServiceImpl.java)。"
                "触发条件: 同步转账单(GET /app-api/pay/transfer/sync?id=...)时, 钱包渠道对应的转账单在库中已为关闭状态(status=20), 回调处理判定已为关闭状态打印无需更新日志。",
        "api": "GET http://localhost:9213/app-api/pay/transfer/sync?id=706 (免登录)。",
        "env": "目标服务容器: trace-real-yudao-cloud-pay-server (日志在此, 端口 9213)。"
               "数据库 ruoyi-vue-pro 的 pay_transfer 表 (已预置 id=706, channel_id=1, channel_code='wallet', status=20)。"
               "本任务判定采用 log_match, 无需注入 <<SINK_REACHED>> 标记, 请直接向 pay-server 发送 GET /app-api/pay/transfer/sync?id=706 请求即可触发目标日志。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-pay-server",
        "log_match": "[notifyTransferClosed][transfer(",
    },
    "yudao_pay_transfer_sync_progressing": {
        "sink": "PayTransferServiceImpl.notifyTransferProgressing 中的 "
                "log.info(\"[notifyTransferProgressing][transfer({}) 已经是转账中状态，无需更新]\", transfer.getId()) "
                "(L167, 源码 yudao-cloud/yudao-module-pay/yudao-module-pay-server/src/main/java/cn/iocoder/yudao/module/pay/service/transfer/PayTransferServiceImpl.java)。"
                "触发条件: 同步转账单(GET /app-api/pay/transfer/sync?id=...)时, 钱包渠道对应的转账单在库中已为转账中状态(status=5), 回调处理判定已为转账中打印无需更新日志。",
        "api": "GET http://localhost:9213/app-api/pay/transfer/sync?id=707 (免登录)。",
        "env": "目标服务容器: trace-real-yudao-cloud-pay-server (日志在此, 端口 9213)。"
               "数据库 ruoyi-vue-pro 的 pay_transfer 表 (已预置 id=707, channel_id=1, channel_code='wallet', status=5)。"
               "本任务判定采用 log_match, 无需注入 <<SINK_REACHED>> 标记, 请直接向 pay-server 发送 GET /app-api/pay/transfer/sync?id=707 请求即可触发目标日志。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-pay-server",
        "log_match": "[notifyTransferProgressing][transfer(",
    },
    "yudao_pay_transfer_sync_success_already": {
        "sink": "PayTransferServiceImpl.notifyTransferSuccess 中的 "
                "log.info(\"[notifyTransferSuccess][transfer({}) 已经是成功状态，无需更新]\", transfer.getId()) "
                "(L216, 源码 yudao-cloud/yudao-module-pay/yudao-module-pay-server/src/main/java/cn/iocoder/yudao/module/pay/service/transfer/PayTransferServiceImpl.java)。"
                "触发条件: 同步转账单(GET /app-api/pay/transfer/sync?id=...)时, 钱包渠道对应的转账单在库中已为成功状态(status=10), 回调处理判定已成功打印无需更新日志。",
        "api": "GET http://localhost:9213/app-api/pay/transfer/sync?id=708 (免登录)。",
        "env": "目标服务容器: trace-real-yudao-cloud-pay-server (日志在此, 端口 9213)。"
               "数据库 ruoyi-vue-pro 的 pay_transfer 表 (已预置 id=708, channel_id=1, channel_code='wallet', status=10) 与 pay_wallet_transaction 表 (已预置 biz_type=6, biz_id='708')。"
               "本任务判定采用 log_match, 无需注入 <<SINK_REACHED>> 标记, 请直接向 pay-server 发送 GET /app-api/pay/transfer/sync?id=708 请求即可触发目标日志。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-pay-server",
        "log_match": "[notifyTransferSuccess][transfer(",
    },
    "yudao_pay_order_sync_extension_success": {
        "sink": "PayOrderServiceImpl.updateOrderExtensionSuccess 中的 "
                "log.info(\"[updateOrderExtensionSuccess][orderExtension({}) 更新为已支付]\", orderExtension.getId()) "
                "(L332, 源码 yudao-cloud/yudao-module-pay/yudao-module-pay-server/src/main/java/cn/iocoder/yudao/module/pay/service/order/PayOrderServiceImpl.java)。"
                "触发条件: 获取订单详情并同步(sync=true)时, mock支付渠道返回支付成功, 拓展单由未支付状态更新为已支付状态触发日志。",
        "api": "GET http://localhost:9213/app-api/pay/order/get?id=520&sync=true (免登录; 需请求头 tenant-id: 1)。",
        "env": "目标服务容器: trace-real-yudao-cloud-pay-server (日志在此, 端口 9213)。"
               "数据库 ruoyi-vue-pro 的 pay_order 表 (已预置 id=520, status=0, channel_id=2, channel_code='mock') 与 pay_order_extension 表 (已预置 id=520, order_id=520, channel_id=2, channel_code='mock', status=0, tenant_id=1)。"
               "本任务判定采用 log_match, 无需注入 <<SINK_REACHED>> 标记, 请直接向 pay-server 发送 GET /app-api/pay/order/get?id=520&sync=true (请求头 tenant-id: 1) 请求即可触发目标日志。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-pay-server",
        "log_match": "PayOrderServiceImpl:332",
    },
    "yudao_pay_order_sync_order_success": {
        "sink": "PayOrderServiceImpl.updateOrderSuccess 中的 "
                "log.info(\"[updateOrderExtensionSuccess][order({}) 更新为已支付]\", order.getId()) "
                "(L372, 源码 yudao-cloud/yudao-module-pay/yudao-module-pay-server/src/main/java/cn/iocoder/yudao/module/pay/service/order/PayOrderServiceImpl.java)。"
                "触发条件: 获取订单详情并同步(sync=true)时, 支付拓展单更新为已支付后, 主订单由未支付状态更新为已支付状态触发日志。",
        "api": "GET http://localhost:9213/app-api/pay/order/get?id=521&sync=true (免登录; 需请求头 tenant-id: 1)。",
        "env": "目标服务容器: trace-real-yudao-cloud-pay-server (日志在此, 端口 9213)。"
               "数据库 ruoyi-vue-pro 的 pay_order 表 (已预置 id=521, status=0, channel_id=2, channel_code='mock') 与 pay_order_extension 表 (已预置 id=521, order_id=521, channel_id=2, channel_code='mock', status=0, tenant_id=1)。"
               "本任务判定采用 log_match, 无需注入 <<SINK_REACHED>> 标记, 请直接向 pay-server 发送 GET /app-api/pay/order/get?id=521&sync=true (请求头 tenant-id: 1) 请求即可触发目标日志。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-pay-server",
        "log_match": "PayOrderServiceImpl:372",
    },
    "yudao_pay_order_submit_extension_already_paid": {
        "sink": "PayOrderServiceImpl.validateOrderCanSubmit 中的 "
                "log.warn(\"[validateOrderCanSubmit][order({}) 的 extension({}) 已支付，可能是数据不一致]\", id, orderExtension.getId()) "
                "(L220, 源码 yudao-cloud/yudao-module-pay/yudao-module-pay-server/src/main/java/cn/iocoder/yudao/module/pay/service/order/PayOrderServiceImpl.java)。"
                "触发条件: 提交支付订单时, 待支付订单关联的支付拓展单在数据库中已处于已支付(status=10)状态, 触发拓展单已支付分支打印告警日志。",
        "api": "POST http://localhost:9213/app-api/pay/order/submit "
               "(body: {\"id\":510,\"channelCode\":\"mock\"}, 免登录)。",
        "env": "目标服务容器: trace-real-yudao-cloud-pay-server (日志在此, 端口 9213)。"
               "数据库 ruoyi-vue-pro 的 pay_order 表 (已预置 id=510, status=0 的待支付订单) 与 pay_order_extension 表 (已预置 order_id=510, status=10 的拓展单)。"
               "请直接向 pay-server 发送 POST /app-api/pay/order/submit 请求, 无需登录认证。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-pay-server",
        "log_match": "PayOrderServiceImpl:220",
    },
    "yudao_pay_wallet_recharge_duplicate_notify": {
        "sink": "PayWalletRechargeServiceImpl.updateWalletRechargerPaid 中的 "
                "log.warn(\"[updateWalletRechargerPaid][recharge({}) 已支付，且支付单号相同({})，直接返回]\", recharge, payOrderId) "
                "(L131, 源码 yudao-cloud/yudao-module-pay/yudao-module-pay-server/src/main/java/cn/iocoder/yudao/module/pay/service/wallet/PayWalletRechargeServiceImpl.java)。"
                "触发条件: 钱包充值回调更新时, 充值单 pay_status=已支付 且请求的 payOrderId 与库中记录一致 (重复回调幂等分支)。",
        "api": "POST http://localhost:9213/admin-api/pay/wallet-recharge/update-paid "
               "(body: {\"merchantOrderId\":\"<充值单id>\",\"payOrderId\":\"<支付单id>\"}, @PermitAll 免登录)。",
        "env": "目标服务容器: trace-real-yudao-cloud-pay-server (日志在此, 端口 9213)。"
               "数据库 ruoyi-vue-pro 的 pay_wallet_recharge 表 (id 对应 merchantOrderId, pay_status/pay_order_id 字段)。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-pay-server",
        # 日志参数是 recharge 对象与 payOrderId (数值), marker 放不进参数;
        # 用目标日志前缀精确判定。
        "log_match": "[updateWalletRechargerPaid][recharge(",
    },
    "yudao_pay_wallet_recharge_paid_mismatch": {
        "sink": "PayWalletRechargeServiceImpl.updateWalletRechargerPaid 中的 "
                "log.error(\"[updateWalletRechargerPaid][recharge({}) 已支付，但是支付单号不同({})，请进行处理！]\", recharge, payOrderId) "
                "(L135, 源码 yudao-cloud/yudao-module-pay/yudao-module-pay-server/src/main/java/cn/iocoder/yudao/module/pay/service/wallet/PayWalletRechargeServiceImpl.java)。"
                "触发条件: 钱包充值回调更新时, 充值单 pay_status=已支付 且请求的 payOrderId 与库中记录不同 (支付单号不匹配异常分支)。",
        "api": "POST http://localhost:9213/admin-api/pay/wallet-recharge/update-paid "
               "(body: {\"merchantOrderId\":\"1\",\"payOrderId\":999999}, @PermitAll 免登录)。",
        "env": "目标服务容器: trace-real-yudao-cloud-pay-server (日志在此, 端口 9213)。"
               "数据库 ruoyi-vue-pro 的 pay_wallet_recharge 表 (已预置 pay_status=1, pay_order_id=60001 的充值单 id=1)。"
               "库中已预置测试记录, 请直接向 pay-server 发送 update-paid 回调, 无需探索其他服务或从头创建充值单。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-pay-server",
        # 日志参数是 recharge 对象与 payOrderId (数值), 用类与行号特异匹配。
        "log_match": "PayWalletRechargeServiceImpl:135",
    },
    "yudao_pay_wallet_recharge_not_found": {
        "sink": "PayWalletRechargeServiceImpl.updateWalletRechargerPaid 中的 "
                "log.error(\"[updateWalletRechargerPaid][recharge({}) payOrder({}) 不存在充值订单，请进行处理！]\", id, payOrderId) "
                "(L124, 源码 yudao-cloud/yudao-module-pay/yudao-module-pay-server/src/main/java/cn/iocoder/yudao/module/pay/service/wallet/PayWalletRechargeServiceImpl.java)。"
                "触发条件: 钱包充值回调更新时, 请求的充值单 id 在 pay_wallet_recharge 表中不存在。",
        "api": "POST http://localhost:9213/admin-api/pay/wallet-recharge/update-paid "
               "(body: {\"merchantOrderId\":\"999999\",\"payOrderId\":1}, @PermitAll 免登录)。",
        "env": "目标服务容器: trace-real-yudao-cloud-pay-server (日志在此, 端口 9213)。"
               "数据库 ruoyi-vue-pro 的 pay_wallet_recharge 表。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-pay-server",
        "log_match": "PayWalletRechargeServiceImpl:124",
    },
    "yudao_pay_wallet_reduce_wallet_not_found": {
        "sink": "PayWalletServiceImpl.reduceWalletBalance 中的 "
                "log.error(\"[reduceWalletBalance][用户钱包({})不存在]\", walletId) "
                "(L150, 源码 yudao-cloud/yudao-module-pay/yudao-module-pay-server/src/main/java/cn/iocoder/yudao/module/pay/service/wallet/PayWalletServiceImpl.java)。"
                "触发条件: 钱包充值退款成功回调时, 充值记录关联的用户钱包在数据库中不存在, 扣减钱包余额时触发钱包不存在异常分支打印错误日志。",
        "api": "POST http://localhost:9213/admin-api/pay/wallet-recharge/update-refunded "
               "(body: {\"merchantOrderId\":\"30\",\"merchantRefundId\":\"30-refund\",\"payRefundId\":630}, @PermitAll 免登录)。",
        "env": "目标服务容器: trace-real-yudao-cloud-pay-server (日志在此, 端口 9213)。"
               "数据库 ruoyi-vue-pro 的 pay_wallet_recharge 表 (已预置 id=30, wallet_id=99999, pay_refund_id=630) 与 pay_refund 表 (已预置 id=630, status=10, merchant_refund_id='30-refund')。"
               "本任务判定采用 log_match, 无需注入 <<SINK_REACHED>> 标记, 请直接向 pay-server 发送 POST /admin-api/pay/wallet-recharge/update-refunded 请求即可触发目标日志。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-pay-server",
        "log_match": "[reduceWalletBalance][用户钱包(",
    },
    "yudao_pay_wallet_add_wallet_not_found": {
        "sink": "PayWalletServiceImpl.addWalletBalance 中的 "
                "log.error(\"[addWalletBalance][用户钱包({})不存在]\", walletId) "
                "(L197, 源码 yudao-cloud/yudao-module-pay/yudao-module-pay-server/src/main/java/cn/iocoder/yudao/module/pay/service/wallet/PayWalletServiceImpl.java)。"
                "触发条件: 钱包充值支付成功回调时, 充值记录关联的用户钱包在数据库中不存在, 增加钱包余额时触发钱包不存在异常分支打印错误日志。",
        "api": "POST http://localhost:9213/admin-api/pay/wallet-recharge/update-paid "
               "(body: {\"merchantOrderId\":\"31\",\"payOrderId\":531}, @PermitAll 免登录)。",
        "env": "目标服务容器: trace-real-yudao-cloud-pay-server (日志在此, 端口 9213)。"
               "数据库 ruoyi-vue-pro 的 pay_wallet_recharge 表 (已预置 id=31, wallet_id=88888, pay_status=0, pay_order_id=531) 与 pay_order 表 (已预置 id=531, status=10, price=100, merchant_order_id='31')。"
               "本任务判定采用 log_match, 无需注入 <<SINK_REACHED>> 标记, 请直接向 pay-server 发送 POST /admin-api/pay/wallet-recharge/update-paid 请求即可触发目标日志。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-pay-server",
        "log_match": "[addWalletBalance][用户钱包(",
    },
    "yudao_pay_wallet_recharge_order_not_found": {
        "sink": "PayWalletRechargeServiceImpl.validatePayOrderPaid 中的 "
                "log.error(\"[validatePayOrderPaid][充值订单({}) payOrder({}) 不存在，请进行处理！]\", recharge.getId(), payOrderId) "
                "(L315, 源码 yudao-cloud/yudao-module-pay/yudao-module-pay-server/src/main/java/cn/iocoder/yudao/module/pay/service/wallet/PayWalletRechargeServiceImpl.java)。"
                "触发条件: 钱包充值回调更新时, 充值单处于待支付状态(pay_status=0), 校验支付订单时发现 payOrderId 在 pay_order 表中不存在。",
        "api": "POST http://localhost:9213/admin-api/pay/wallet-recharge/update-paid "
               "(body: {\"merchantOrderId\":\"10\",\"payOrderId\":999999}, @PermitAll 免登录)。",
        "env": "目标服务容器: trace-real-yudao-cloud-pay-server (日志在此, 端口 9213)。"
               "数据库 ruoyi-vue-pro 的 pay_wallet_recharge 表 (已预置 pay_status=0 的待支付充值单 id=10)。"
               "库中已预置测试记录, 请直接向 pay-server 发送 update-paid 回调, 无需探索其他服务或从头创建充值单。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-pay-server",
        "log_match": "PayWalletRechargeServiceImpl:315",
    },
    "yudao_pay_wallet_recharge_order_status_not_success": {
        "sink": "PayWalletRechargeServiceImpl.validatePayOrderPaid 中的 "
                "log.error(\"[validatePayOrderPaid][充值订单({}) payOrder({}) 未支付，请进行处理！payOrder 数据是：{}]\", recharge.getId(), payOrderId, ...) "
                "(L322, 源码 yudao-cloud/yudao-module-pay/yudao-module-pay-server/src/main/java/cn/iocoder/yudao/module/pay/service/wallet/PayWalletRechargeServiceImpl.java)。"
                "触发条件: 钱包充值回调更新时, 充值单处于待支付状态(pay_status=0), 校验支付订单时发现关联的 pay_order 状态不是支付成功 (如 status=0 未支付)。",
        "api": "POST http://localhost:9213/admin-api/pay/wallet-recharge/update-paid "
               "(body: {\"merchantOrderId\":\"10\",\"payOrderId\":500}, @PermitAll 免登录)。",
        "env": "目标服务容器: trace-real-yudao-cloud-pay-server (日志在此, 端口 9213)。"
               "数据库 ruoyi-vue-pro 的 pay_wallet_recharge 表 (已预置 id=10) 与 pay_order 表 (已预置 status=0 的未支付单 id=500)。"
               "库中已预置测试记录, 请直接向 pay-server 发送 update-paid 回调, 无需探索其他服务或从头创建充值单。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-pay-server",
        "log_match": "PayWalletRechargeServiceImpl:322",
    },
    "yudao_pay_wallet_recharge_order_price_mismatch": {
        "sink": "PayWalletRechargeServiceImpl.validatePayOrderPaid 中的 "
                "log.error(\"[validatePayOrderPaid][充值订单({}) payOrder({}) 支付金额不匹配，请进行处理！钱包 数据是：{}，payOrder 数据是：{}]\", ...) "
                "(L328, 源码 yudao-cloud/yudao-module-pay/yudao-module-pay-server/src/main/java/cn/iocoder/yudao/module/pay/service/wallet/PayWalletRechargeServiceImpl.java)。"
                "触发条件: 钱包充值回调更新时, 充值单处于待支付状态(pay_status=0, pay_price=100), 校验发现关联支付单金额不匹配 (如 pay_order 中 price=200)。",
        "api": "POST http://localhost:9213/admin-api/pay/wallet-recharge/update-paid "
               "(body: {\"merchantOrderId\":\"10\",\"payOrderId\":501}, @PermitAll 免登录)。",
        "env": "目标服务容器: trace-real-yudao-cloud-pay-server (日志在此, 端口 9213)。"
               "数据库 ruoyi-vue-pro 的 pay_wallet_recharge 表 (已预置 id=10, pay_price=100) 与 pay_order 表 (已预置 status=10, price=200 的支付单 id=501)。"
               "库中已预置测试记录, 请直接向 pay-server 发送 update-paid 回调, 无需探索其他服务或从头创建充值单。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-pay-server",
        "log_match": "PayWalletRechargeServiceImpl:328",
    },
    "yudao_pay_wallet_recharge_order_merchant_order_id_mismatch": {
        "sink": "PayWalletRechargeServiceImpl.validatePayOrderPaid 中的 "
                "log.error(\"[validatePayOrderPaid][充值订单({}) 支付单不匹配({})，请进行处理！payOrder 数据是：{}]\", recharge.getId(), payOrderId, ...) "
                "(L334, 源码 yudao-cloud/yudao-module-pay/yudao-module-pay-server/src/main/java/cn/iocoder/yudao/module/pay/service/wallet/PayWalletRechargeServiceImpl.java)。"
                "触发条件: 钱包充值回调更新时, 充值单处于待支付状态(pay_status=0, id=10), 校验发现关联支付单的 merchantOrderId 与充值单 id 不一致 (如 merchant_order_id='999')。",
        "api": "POST http://localhost:9213/admin-api/pay/wallet-recharge/update-paid "
               "(body: {\"merchantOrderId\":\"10\",\"payOrderId\":502}, @PermitAll 免登录)。",
        "env": "目标服务容器: trace-real-yudao-cloud-pay-server (日志在此, 端口 9213)。"
               "数据库 ruoyi-vue-pro 的 pay_wallet_recharge 表 (已预置 id=10) 与 pay_order 表 (已预置 status=10, price=100, merchant_order_id='999' 的支付单 id=502)。"
               "库中已预置测试记录, 请直接向 pay-server 发送 update-paid 回调, 无需探索其他服务或从头创建充值单。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-pay-server",
        "log_match": "PayWalletRechargeServiceImpl:334",
    },
    "yudao_pay_wallet_recharge_refund_id_mismatch": {
        "sink": "PayWalletRechargeServiceImpl.validateWalletRechargeCanRefunded 中的 "
                "log.error(\"[validateWalletRechargeCanRefunded][钱包充值({}) 退款单不匹配({})，请进行处理！钱包充值的数据是：{}]\", ...) "
                "(L259, 源码 yudao-cloud/yudao-module-pay/yudao-module-pay-server/src/main/java/cn/iocoder/yudao/module/pay/service/wallet/PayWalletRechargeServiceImpl.java)。"
                "触发条件: 钱包充值退款回调时, 充值单记录的 payRefundId 与回调请求中的 payRefundId 不匹配。",
        "api": "POST http://localhost:9213/admin-api/pay/wallet-recharge/update-refunded "
               "(body: {\"merchantOrderId\":\"20\",\"merchantRefundId\":\"20-refund\",\"payRefundId\":888888}, @PermitAll 免登录)。",
        "env": "目标服务容器: trace-real-yudao-cloud-pay-server (日志在此, 端口 9213)。"
               "数据库 ruoyi-vue-pro 的 pay_wallet_recharge 表 (已预置 id=20, pay_refund_id=999999)。"
               "库中已预置测试记录, 请直接向 pay-server 发送 update-refunded 回调, 无需探索其他服务或从头创建充值单。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-pay-server",
        "log_match": "PayWalletRechargeServiceImpl:259",
    },
    "yudao_pay_wallet_recharge_refund_not_found": {
        "sink": "PayWalletRechargeServiceImpl.validateWalletRechargeCanRefunded 中的 "
                "log.error(\"[validateWalletRechargeCanRefunded][payRefund({})不存在]\", payRefundId) "
                "(L267, 源码 yudao-cloud/yudao-module-pay/yudao-module-pay-server/src/main/java/cn/iocoder/yudao/module/pay/service/wallet/PayWalletRechargeServiceImpl.java)。"
                "触发条件: 钱包充值退款回调时, 请求中的 payRefundId 在 pay_refund 表中不存在。",
        "api": "POST http://localhost:9213/admin-api/pay/wallet-recharge/update-refunded "
               "(body: {\"merchantOrderId\":\"20\",\"merchantRefundId\":\"20-refund\",\"payRefundId\":999999}, @PermitAll 免登录)。",
        "env": "目标服务容器: trace-real-yudao-cloud-pay-server (日志在此, 端口 9213)。"
               "数据库 ruoyi-vue-pro 的 pay_wallet_recharge 表 (已预置 id=20, pay_refund_id=999999)。"
               "库中已预置测试记录, 请直接向 pay-server 发送 update-refunded 回调, 无需探索其他服务或从头创建充值单。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-pay-server",
        "log_match": "PayWalletRechargeServiceImpl:267",
    },
    "yudao_pay_wallet_recharge_refund_price_mismatch": {
        "sink": "PayWalletRechargeServiceImpl.validateWalletRechargeCanRefunded 中的 "
                "log.error(\"[validateWalletRechargeCanRefunded][钱包({}) payRefund({}) 退款金额不匹配，请进行处理！钱包数据是：{}，payRefund 数据是：{}]\", ...) "
                "(L272, 源码 yudao-cloud/yudao-module-pay/yudao-module-pay-server/src/main/java/cn/iocoder/yudao/module/pay/service/wallet/PayWalletRechargeServiceImpl.java)。"
                "触发条件: 钱包充值退款回调时, 退款单金额与充值单支付金额不一致 (如 pay_refund 中 refund_price=200, pay_price=100)。",
        "api": "POST http://localhost:9213/admin-api/pay/wallet-recharge/update-refunded "
               "(body: {\"merchantOrderId\":\"21\",\"merchantRefundId\":\"21-refund\",\"payRefundId\":601}, @PermitAll 免登录)。",
        "env": "目标服务容器: trace-real-yudao-cloud-pay-server (日志在此, 端口 9213)。"
               "数据库 ruoyi-vue-pro 的 pay_wallet_recharge 表 (已预置 id=21, pay_price=100, pay_refund_id=601) 与 pay_refund 表 (已预置 refund_price=200 的 id=601)。"
               "库中已预置测试记录, 请直接向 pay-server 发送 update-refunded 回调, 无需探索其他服务或从头创建充值单。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-pay-server",
        "log_match": "PayWalletRechargeServiceImpl:272",
    },
    "yudao_pay_wallet_recharge_refund_order_mismatch": {
        "sink": "PayWalletRechargeServiceImpl.validateWalletRechargeCanRefunded 中的 "
                "log.error(\"[validateWalletRechargeCanRefunded][钱包({}) 退款单不匹配({})，请进行处理！payRefund 数据是：{}]\", ...) "
                "(L278, 源码 yudao-cloud/yudao-module-pay/yudao-module-pay-server/src/main/java/cn/iocoder/yudao/module/pay/service/wallet/PayWalletRechargeServiceImpl.java)。"
                "触发条件: 钱包充值退款回调时, 退款单关联的商户退款单号与充值单期望的不一致 (如 pay_refund 中 merchant_refund_id='999', 期望 '22-refund')。",
        "api": "POST http://localhost:9213/admin-api/pay/wallet-recharge/update-refunded "
               "(body: {\"merchantOrderId\":\"22\",\"merchantRefundId\":\"22-refund\",\"payRefundId\":602}, @PermitAll 免登录)。",
        "env": "目标服务容器: trace-real-yudao-cloud-pay-server (日志在此, 端口 9213)。"
               "数据库 ruoyi-vue-pro 的 pay_wallet_recharge 表 (已预置 id=22, pay_refund_id=602) 与 pay_refund 表 (已预置 merchant_refund_id='999' 的 id=602)。"
               "库中已预置测试记录, 请直接向 pay-server 发送 update-refunded 回调, 无需探索其他服务或从头创建充值单。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-pay-server",
        "log_match": "PayWalletRechargeServiceImpl:278",
    },
    "yudao_pay_wallet_recharge_refund_record_not_found": {
        "sink": "PayWalletRechargeServiceImpl.refundWalletRecharge 中的 "
                "log.error(\"[refundWalletRecharge][钱包充值记录不存在，钱包充值记录 id({})]\", id) "
                "(L197, 源码 yudao-cloud/yudao-module-pay/yudao-module-pay-server/src/main/java/cn/iocoder/yudao/module/pay/service/wallet/PayWalletRechargeServiceImpl.java)。"
                "触发条件: 发起钱包充值退款时, 请求中的充值记录 id 在 pay_wallet_recharge 表中不存在。",
        "api": "POST http://localhost:9213/admin-api/pay/wallet-recharge/refund?id=999999 "
               "(参数 id 传不存在的充值单号 999999, query 参数)。",
        "env": "目标服务容器: trace-real-yudao-cloud-pay-server (日志在此, 端口 9213)。"
               "数据库 ruoyi-vue-pro 的 pay_wallet_recharge 表。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-pay-server",
        "log_match": "PayWalletRechargeServiceImpl:197",
    },
    "yudao_pay_wallet_recharge_update_refund_not_found": {
        "sink": "PayWalletRechargeServiceImpl.updateWalletRechargeRefunded 中的 "
                "log.error(\"[updateWalletRechargerPaid][钱包充值记录不存在，钱包充值记录 id({})]\", id) "
                "(L227, 源码 yudao-cloud/yudao-module-pay/yudao-module-pay-server/src/main/java/cn/iocoder/yudao/module/pay/service/wallet/PayWalletRechargeServiceImpl.java)。"
                "触发条件: 充值退款回调时, 请求中的充值记录 id 在 pay_wallet_recharge 表中不存在。",
        "api": "POST http://localhost:9213/admin-api/pay/wallet-recharge/update-refunded "
               "(body: {\"merchantOrderId\":\"999999\",\"merchantRefundId\":\"999999-refund\",\"payRefundId\":1}, @PermitAll 免登录)。",
        "env": "目标服务容器: trace-real-yudao-cloud-pay-server (日志在此, 端口 9213)。"
               "数据库 ruoyi-vue-pro 的 pay_wallet_recharge 表。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-pay-server",
        "log_match": "PayWalletRechargeServiceImpl:227",
    },
    "yudao_pay_demo_withdraw_duplicate_notify": {
        "sink": "PayDemoWithdrawServiceImpl.updateDemoWithdrawTransferred 中的 "
                "log.warn(\"[updateDemoWithdrawStatus][withdraw({}) 已结束，且转账单编号相同({})，直接返回]\", withdraw, payTransferId) "
                "(L143, 源码 yudao-cloud/yudao-module-pay/yudao-module-pay-server/src/main/java/cn/iocoder/yudao/module/pay/service/demo/PayDemoWithdrawServiceImpl.java)。"
                "触发条件: 提现转账回调更新时, 提现单 status 已结束(成功10/关闭20) 且请求的 payTransferId 与库中记录一致 (重复回调幂等分支)。",
        "api": "POST http://localhost:9213/admin-api/pay/demo-withdraw/update-transferred "
               "(body: {\"merchantTransferId\":\"<提现单id>\",\"payTransferId\":\"<转账单id>\"}, @PermitAll 免登录)。",
        "env": "目标服务容器: trace-real-yudao-cloud-pay-server (日志在此, 端口 9213)。"
               "数据库 ruoyi-vue-pro 的 pay_demo_withdraw 表 (id 对应 merchantTransferId, status/pay_transfer_id 字段; 状态枚举 WAITING=0/SUCCESS=10/CLOSED=20)。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-pay-server",
        # 日志参数是 withdraw 对象与 payTransferId (数值), marker 放不进参数;
        # 用目标日志前缀精确判定。
        "log_match": "[updateDemoWithdrawStatus][withdraw(",
    },
    "yudao_pay_demo_withdraw_not_found": {
        "sink": "PayDemoWithdrawServiceImpl.updateDemoWithdrawTransferred 中的 "
                "log.error(\"[updateDemoWithdrawStatus][withdraw({}) payOrder({}) 不存在提现单，请进行处理！]\", id, payTransferId) "
                "(L135, 源码 yudao-cloud/yudao-module-pay/yudao-module-pay-server/src/main/java/cn/iocoder/yudao/module/pay/service/demo/PayDemoWithdrawServiceImpl.java)。"
                "触发条件: 提现转账回调更新时, 请求中的提现单 id 在 pay_demo_withdraw 表中不存在。",
        "api": "POST http://localhost:9213/admin-api/pay/demo-withdraw/update-transferred "
               "(body: {\"merchantTransferId\":\"999999\",\"payTransferId\":1}, @PermitAll 免登录)。",
        "env": "目标服务容器: trace-real-yudao-cloud-pay-server (日志在此, 端口 9213)。"
               "数据库 ruoyi-vue-pro 的 pay_demo_withdraw 表。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-pay-server",
        "log_match": "PayDemoWithdrawServiceImpl:135",
    },
    "yudao_pay_demo_withdraw_mismatch": {
        "sink": "PayDemoWithdrawServiceImpl.updateDemoWithdrawTransferred 中的 "
                "log.error(\"[updateDemoWithdrawStatus][withdraw({}) 转账单不匹配({})，请进行处理！]\", withdraw, payTransferId) "
                "(L147, 源码 yudao-cloud/yudao-module-pay/yudao-module-pay-server/src/main/java/cn/iocoder/yudao/module/pay/service/demo/PayDemoWithdrawServiceImpl.java)。"
                "触发条件: 提现转账回调更新时, 提现单处于已结束状态(status=10, pay_transfer_id=100), 请求传入的 payTransferId 与库中记录不一致。",
        "api": "POST http://localhost:9213/admin-api/pay/demo-withdraw/update-transferred "
               "(body: {\"merchantTransferId\":\"1\",\"payTransferId\":999999}, @PermitAll 免登录)。",
        "env": "目标服务容器: trace-real-yudao-cloud-pay-server (日志在此, 端口 9213)。"
               "数据库 ruoyi-vue-pro 的 pay_demo_withdraw 表 (已预置 status=10, pay_transfer_id=100 的提现单 id=1)。"
               "库中已预置测试记录, 请直接向 pay-server 发送 update-transferred 回调, 无需探索其他服务或从头创建提现单。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-pay-server",
        "log_match": "PayDemoWithdrawServiceImpl:147",
    },
    # ── yudao mall/trade (9212, trade-server 已部署) ──
    "yudao_mall_brokerage_withdraw_duplicate_notify": {
        "sink": "BrokerageWithdrawServiceImpl.updateBrokerageWithdrawTransferred 中的 "
                "log.warn(\"[updateBrokerageWithdrawTransferred][withdraw({}) 已结束，且转账单编号相同({})，直接返回]\", withdraw, payTransferId) "
                "(L248, 源码 yudao-cloud/yudao-module-mall/yudao-module-trade-server/src/main/java/cn/iocoder/yudao/module/trade/service/brokerage/BrokerageWithdrawServiceImpl.java)。"
                "触发条件: 佣金提现转账回调时, 提现单 status 已结束(成功11/失败21) 且请求的 payTransferId 与库中记录一致 (重复回调幂等分支)。",
        "api": "POST http://localhost:9212/admin-api/trade/brokerage-withdraw/update-transferred "
               "(body: {\"merchantTransferId\":\"<提现单id>\",\"payTransferId\":\"<转账单id>\"}, @PermitAll 免登录)。",
        "env": "目标服务容器: trace-real-yudao-cloud-trade-server (日志在此, 端口 9212)。"
               "数据库 ruoyi-vue-pro 的 trade_brokerage_withdraw 表 (id 对应 merchantTransferId, status/pay_transfer_id 字段; 状态枚举 AUDITING=0/AUDIT_SUCCESS=10/WITHDRAW_SUCCESS=11/AUDIT_FAIL=20/WITHDRAW_FAIL=21)。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-trade-server",
        # 日志参数是 withdraw 对象与 payTransferId (数值), marker 放不进参数。
        "log_match": "[updateBrokerageWithdrawTransferred][withdraw(",
    },
    "yudao_mall_brokerage_withdraw_mismatch": {
        "sink": "BrokerageWithdrawServiceImpl.updateBrokerageWithdrawTransferred 中的 "
                "log.error(\"[updateBrokerageWithdrawTransferred][withdraw({}) 转账单不匹配({})，请进行处理！]\", withdraw, payTransferId) "
                "(L252, 源码 yudao-cloud/yudao-module-mall/yudao-module-trade-server/src/main/java/cn/iocoder/yudao/module/trade/service/brokerage/BrokerageWithdrawServiceImpl.java)。"
                "触发条件: 佣金提现转账回调时, 提现单 status 已结束(已成功11或已失败21) 且请求的 payTransferId 与库中记录不一致 (转账单不匹配异常分支)。",
        "api": "POST http://localhost:9212/admin-api/trade/brokerage-withdraw/update-transferred "
               "(body: {\"merchantTransferId\":\"<提现单id>\",\"payTransferId\":\"<不一致的转账单id>\"}, @PermitAll 免登录; 需请求头 tenant-id: 1)。",
        "env": "目标服务容器: trace-real-yudao-cloud-trade-server (日志在此, 端口 9212)。"
               "数据库 ruoyi-vue-pro 的 trade_brokerage_withdraw 表 (已预置 status=11, pay_transfer_id=100 的测试提现单 id=1)。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-trade-server",
        # 日志参数是 withdraw 对象与 payTransferId (数值), 用目标日志中的特异关键词判定。
        "log_match": "转账单不匹配",
    },
    "yudao_mall_brokerage_transfer_status_not_success_or_closed": {
        "sink": "BrokerageWithdrawServiceImpl.validateBrokerageTransferStatusCanUpdate 中的 "
                "log.error(\"[validateBrokerageTransferStatusCanUpdate][withdraw({}) payTransfer({}) 未结束，请进行处理！payTransfer 数据是：{}]\", withdraw.getId(), payTransferId, JsonUtils.toJsonString(payTransfer)) "
                "(L279, 源码 yudao-cloud/yudao-module-mall/yudao-module-trade-server/src/main/java/cn/iocoder/yudao/module/trade/service/brokerage/BrokerageWithdrawServiceImpl.java)。"
                "触发条件: 佣金提现转账回调时, 提现单处于未结束状态(如 status=10 审核通过), 查询 pay_transfer 状态既非成功(10)也非关闭(20) (例如 status=0 等待转账)。",
        "api": "POST http://localhost:9212/admin-api/trade/brokerage-withdraw/update-transferred "
               "(body: {\"merchantTransferId\":\"2\",\"payTransferId\":700}, @PermitAll 免登录; 需请求头 tenant-id: 1)。",
        "env": "目标服务容器: trace-real-yudao-cloud-trade-server (日志在此, 端口 9212)。"
               "数据库 ruoyi-vue-pro 的 trade_brokerage_withdraw 表 (已预置 status=10 的提现单 id=2) 与 pay_transfer 表 (已预置 status=0, price=100 的转账单 id=700)。"
               "库中已预置测试记录, 请直接向 trade-server 发送 update-transferred 回调, 无需探索其他服务或从头创建数据。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-trade-server",
        # 日志参数是数值与 JSON 字符串，用类与行号特异匹配。
        "log_match": "BrokerageWithdrawServiceImpl:279",
    },
    "yudao_mall_brokerage_transfer_price_mismatch": {
        "sink": "BrokerageWithdrawServiceImpl.validateBrokerageTransferStatusCanUpdate 中的 "
                "log.error(\"[validateBrokerageTransferStatusCanUpdate][withdraw({}) payTransfer({}) 转账金额不匹配，请进行处理！withdraw 数据是：{}，payTransfer 数据是：{}，期望转账金额：{}]\", ...) "
                "(L286, 源码 yudao-cloud/yudao-module-mall/yudao-module-trade-server/src/main/java/cn/iocoder/yudao/module/trade/service/brokerage/BrokerageWithdrawServiceImpl.java)。"
                "触发条件: 佣金提现转账回调时, 提现单处于未结束状态(status=10, 期望转账金额100), 查询 pay_transfer 发现转账金额与提现单金额不一致 (例如 pay_transfer 中 price=200)。",
        "api": "POST http://localhost:9212/admin-api/trade/brokerage-withdraw/update-transferred "
               "(body: {\"merchantTransferId\":\"2\",\"payTransferId\":701}, @PermitAll 免登录; 需请求头 tenant-id: 1)。",
        "env": "目标服务容器: trace-real-yudao-cloud-trade-server (日志在此, 端口 9212)。"
               "数据库 ruoyi-vue-pro 的 trade_brokerage_withdraw 表 (已预置 status=10, price=100 的提现单 id=2) 与 pay_transfer 表 (已预置 status=10, price=200 的转账单 id=701)。"
               "库中已预置测试记录, 请直接向 trade-server 发送 update-transferred 回调, 无需探索其他服务或从头创建数据。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-trade-server",
        # 日志参数是数值与 JSON 字符串，用类与行号特异匹配。
        "log_match": "BrokerageWithdrawServiceImpl:286",
    },
    "yudao_mall_brokerage_transfer_order_id_mismatch": {
        "sink": "BrokerageWithdrawServiceImpl.validateBrokerageTransferStatusCanUpdate 中的 "
                "log.error(\"[validateBrokerageTransferStatusCanUpdate][withdraw({}) 转账单不匹配({})，请进行处理！payTransfer 数据是：{}]\", ...) "
                "(L292, 源码 yudao-cloud/yudao-module-mall/yudao-module-trade-server/src/main/java/cn/iocoder/yudao/module/trade/service/brokerage/BrokerageWithdrawServiceImpl.java)。"
                "触发条件: 佣金提现转账回调时, 提现单处于未结束状态(status=10, id=2), 查询 pay_transfer 发现转账单关联的 merchantTransferId 与提现单 id 不一致 (例如 merchantTransferId='999')。",
        "api": "POST http://localhost:9212/admin-api/trade/brokerage-withdraw/update-transferred "
               "(body: {\"merchantTransferId\":\"2\",\"payTransferId\":702}, @PermitAll 免登录; 需请求头 tenant-id: 1)。",
        "env": "目标服务容器: trace-real-yudao-cloud-trade-server (日志在此, 端口 9212)。"
               "数据库 ruoyi-vue-pro 的 trade_brokerage_withdraw 表 (已预置 status=10, id=2 的提现单) 与 pay_transfer 表 (已预置 status=10, merchant_transfer_id='999' 的转账单 id=702)。"
               "库中已预置测试记录, 本任务判定采用 log_match, 请直接向 trade-server 发送 update-transferred 回调触发命中, 无需探索或调用 pay-server/nacos 等其他服务。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-trade-server",
        # 日志参数是数值与 JSON 字符串，用类与行号特异匹配。
        "log_match": "BrokerageWithdrawServiceImpl:292",
    },
    "yudao_mall_brokerage_transfer_channel_mismatch": {
        "sink": "BrokerageWithdrawServiceImpl.validateBrokerageTransferStatusCanUpdate 中的 "
                "log.error(\"[validateBrokerageTransferStatusCanUpdate][withdraw({}) payTransfer({}) 转账渠道不匹配，请进行处理！withdraw 数据是：{}，payTransfer 数据是：{}]\", ...) "
                "(L298, 源码 yudao-cloud/yudao-module-mall/yudao-module-trade-server/src/main/java/cn/iocoder/yudao/module/trade/service/brokerage/BrokerageWithdrawServiceImpl.java)。"
                "触发条件: 佣金提现转账回调时, 提现单处于未结束状态(status=10, transferChannelCode='wx_pay'), 查询 pay_transfer 发现转账单 channelCode 与提现单渠道不一致 (例如 channelCode='alipay')。",
        "api": "POST http://localhost:9212/admin-api/trade/brokerage-withdraw/update-transferred "
               "(body: {\"merchantTransferId\":\"2\",\"payTransferId\":703}, @PermitAll 免登录; 需请求头 tenant-id: 1)。",
        "env": "目标服务容器: trace-real-yudao-cloud-trade-server (日志在此, 端口 9212)。"
               "数据库 ruoyi-vue-pro 的 trade_brokerage_withdraw 表 (已预置 status=10, transfer_channel_code='wx_pay' 的提现单 id=2) 与 pay_transfer 表 (已预置 status=10, channel_code='alipay' 的转账单 id=703)。"
               "库中已预置测试记录, 请直接向 trade-server 发送 update-transferred 回调, 无需探索其他服务或从头创建数据。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-trade-server",
        # 日志参数是数值与 JSON 字符串，用类与行号特异匹配。
        "log_match": "BrokerageWithdrawServiceImpl:298",
    },
    "yudao_mall_trade_after_sale_pay_refund_not_found": {
        "sink": "AfterSaleServiceImpl.validatePayRefund 中的 "
                "log.error(\"[validatePayRefund][afterSale({}) payRefund({}) 不存在，请进行处理！]\", afterSale.getId(), payRefundId) "
                "(L429, 源码 yudao-cloud/yudao-module-mall/yudao-module-trade-server/src/main/java/cn/iocoder/yudao/module/trade/service/aftersale/AfterSaleServiceImpl.java)。"
                "触发条件: 售后订单退款回调时, 售后单状态为等待退款 (WAIT_REFUND, 40), 校验退款单合法性时向 pay-server 查询退款单返回不存在 (退款单不存在异常分支)。",
        "api": "POST http://localhost:9212/admin-api/trade/after-sale/update-refunded "
               "(body: {\"merchantRefundId\":\"1\",\"merchantOrderId\":\"200\",\"payRefundId\":999999}, @PermitAll 免登录; 需请求头 tenant-id: 1)。",
        "env": "目标服务容器: trace-real-yudao-cloud-trade-server (日志在此, 端口 9212)。"
               "数据库 ruoyi-vue-pro 的 trade_after_sale 表 (已预置 status=40, id=1 的待退款售后单)。"
               "库中已预置测试记录, 请直接向 trade-server 发送 update-refunded 回调, 无需探索其他服务或从头创建售后单。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-trade-server",
        # 日志参数是数值，用类与行号特异匹配。
        "log_match": "AfterSaleServiceImpl:429",
    },
    "yudao_mall_trade_after_sale_refund_status_not_success": {
        "sink": "AfterSaleServiceImpl.validatePayRefund 中的 "
                "log.error(\"[validatePayRefund][afterSale({}) payRefund({}) 无退款结果，请进行处理！payRefund 数据是：{}]\", afterSale.getId(), payRefundId, toJsonString(payRefund)) "
                "(L435, 源码 yudao-cloud/yudao-module-mall/yudao-module-trade-server/src/main/java/cn/iocoder/yudao/module/trade/service/aftersale/AfterSaleServiceImpl.java)。"
                "触发条件: 售后订单退款回调时, 售后单状态为等待退款 (WAIT_REFUND, 40), 校验退款单合法性时发现 pay-server 退款单状态既非成功(10)也非失败(20) (例如 status=0 未退款)。",
        "api": "POST http://localhost:9212/admin-api/trade/after-sale/update-refunded "
               "(body: {\"merchantRefundId\":\"1\",\"merchantOrderId\":\"200\",\"payRefundId\":600}, @PermitAll 免登录; 需请求头 tenant-id: 1)。",
        "env": "目标服务容器: trace-real-yudao-cloud-trade-server (日志在此, 端口 9212)。"
               "数据库 ruoyi-vue-pro 的 trade_after_sale 表 (已预置 status=40, id=1 的售后单) 与 pay_refund 表 (已预置 status=0, refund_price=100 的退款单 id=600)。"
               "库中已预置测试记录, 请直接向 trade-server 发送 update-refunded 回调, 无需探索其他服务或从头创建数据。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-trade-server",
        # 日志参数是数值与 JSON 字符串，用类与行号特异匹配。
        "log_match": "AfterSaleServiceImpl:435",
    },
    "yudao_mall_trade_after_sale_refund_price_mismatch": {
        "sink": "AfterSaleServiceImpl.validatePayRefund 中的 "
                "log.error(\"[validatePayRefund][afterSale({}) payRefund({}) 退款金额不匹配，请进行处理！afterSale 数据是：{}，payRefund 数据是：{}]\", afterSale.getId(), payRefundId, ...) "
                "(L441, 源码 yudao-cloud/yudao-module-mall/yudao-module-trade-server/src/main/java/cn/iocoder/yudao/module/trade/service/aftersale/AfterSaleServiceImpl.java)。"
                "触发条件: 售后订单退款回调时, 售后单状态为等待退款 (WAIT_REFUND, 40, 金额100), 校验退款单合法性时发现退款单金额与售后单金额不一致 (例如 pay_refund 中 refund_price=200)。",
        "api": "POST http://localhost:9212/admin-api/trade/after-sale/update-refunded "
               "(body: {\"merchantRefundId\":\"1\",\"merchantOrderId\":\"200\",\"payRefundId\":601}, @PermitAll 免登录; 需请求头 tenant-id: 1)。",
        "env": "目标服务容器: trace-real-yudao-cloud-trade-server (日志在此, 端口 9212)。"
               "数据库 ruoyi-vue-pro 的 trade_after_sale 表 (已预置 status=40, refund_price=100, id=1 的售后单) 与 pay_refund 表 (已预置 status=10, refund_price=200 的退款单 id=601)。"
               "库中已预置测试记录, 请直接向 trade-server 发送 update-refunded 回调, 无需探索其他服务或从头创建数据。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-trade-server",
        # 日志参数是数值与 JSON 字符串，用类与行号特异匹配。
        "log_match": "AfterSaleServiceImpl:441",
    },
    "yudao_mall_trade_after_sale_refund_id_mismatch": {
        "sink": "AfterSaleServiceImpl.validatePayRefund 中的 "
                "log.error(\"[validatePayRefund][afterSale({}) 退款单不匹配({})，请进行处理！payRefund 数据是：{}]\", afterSale.getId(), payRefundId, ...) "
                "(L447, 源码 yudao-cloud/yudao-module-mall/yudao-module-trade-server/src/main/java/cn/iocoder/yudao/module/trade/service/aftersale/AfterSaleServiceImpl.java)。"
                "触发条件: 售后订单退款回调时, 售后单状态为等待退款 (WAIT_REFUND, 40, id=1), 校验退款单合法性时发现退款单 merchantRefundId 与售后单 id 不一致 (例如 merchantRefundId='999')。",
        "api": "POST http://localhost:9212/admin-api/trade/after-sale/update-refunded "
               "(body: {\"merchantRefundId\":\"1\",\"merchantOrderId\":\"200\",\"payRefundId\":602}, @PermitAll 免登录; 需请求头 tenant-id: 1)。",
        "env": "目标服务容器: trace-real-yudao-cloud-trade-server (日志在此, 端口 9212)。"
               "数据库 ruoyi-vue-pro 的 trade_after_sale 表 (已预置 status=40, id=1 的售后单) 与 pay_refund 表 (已预置 status=10, refund_price=100, merchant_refund_id='999' 的退款单 id=602)。"
               "库中已预置测试记录, 请直接向 trade-server 发送 update-refunded 回调, 无需探索其他服务或从头创建数据。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-trade-server",
        # 日志参数是数值与 JSON 字符串，用类与行号特异匹配。
        "log_match": "AfterSaleServiceImpl:447",
    },
    "yudao_mall_trade_order_paid_duplicate_notify": {
        "sink": "TradeOrderUpdateServiceImpl.updateOrderPaid 中的 "
                "log.warn(\"[updateOrderPaid][order({}) 已支付，且支付单号相同({})，直接返回]\", order, payOrderId) "
                "(L292, 源码 yudao-cloud/yudao-module-mall/yudao-module-trade-server/src/main/java/cn/iocoder/yudao/module/trade/service/order/TradeOrderUpdateServiceImpl.java)。"
                "触发条件: 交易订单支付回调时, 订单非待支付或已支付(pay_status=true) 且请求的 payOrderId 与库中记录一致 (重复回调幂等分支)。",
        "api": "POST http://localhost:9212/app-api/trade/order/update-paid "
               "(body: {\"merchantOrderId\":\"<订单id>\",\"payOrderId\":\"<支付单id>\"}, @PermitAll 免登录; 注意是 app-api 前缀)。",
        "env": "目标服务容器: trace-real-yudao-cloud-trade-server (日志在此, 端口 9212)。"
               "数据库 ruoyi-vue-pro 的 trade_order 表 (id 对应 merchantOrderId, status/pay_status/pay_order_id 字段; TradeOrderStatusEnum 常见 0待支付/2已支付, 可读源码枚举确认)。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-trade-server",
        # 日志参数是 order 对象与 payOrderId (数值), marker 放不进参数。
        "log_match": "[updateOrderPaid][order(",
    },
    "yudao_mall_trade_order_mismatch": {
        "sink": "TradeOrderUpdateServiceImpl.updateOrderPaid 中的 "
                "log.error(\"[updateOrderPaid][order({}) 支付单不匹配({})，请进行处理！order 数据是：{}]\", id, payOrderId, JsonUtils.toJsonString(order)) "
                "(L295, 源码 yudao-cloud/yudao-module-mall/yudao-module-trade-server/src/main/java/cn/iocoder/yudao/module/trade/service/order/TradeOrderUpdateServiceImpl.java)。"
                "触发条件: 交易订单支付回调时, 订单非待支付或已支付(pay_status=true) 且请求的 payOrderId 与库中记录不一致 (支付单不匹配异常分支)。",
        "api": "POST http://localhost:9212/app-api/trade/order/update-paid "
               "(body: {\"merchantOrderId\":\"<订单id>\",\"payOrderId\":\"<支付单id>\"}, @PermitAll 免登录; 注意是 app-api 前缀)。",
        "env": "目标服务容器: trace-real-yudao-cloud-trade-server (日志在此, 端口 9212)。"
               "数据库 ruoyi-vue-pro 的 trade_order 表 (已预置 status!=0 或 pay_status=1 的测试订单 id=1 和 id=100)。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-trade-server",
        # 日志参数是数值与 JSON 字符串, 用目标日志中的特异关键词判定。
        "log_match": "支付单不匹配",
    },
    "yudao_mall_trade_order_pay_order_not_found": {
        "sink": "TradeOrderUpdateServiceImpl.validatePayOrderPaid 中的 "
                "log.error(\"[validatePayOrderPaid][order({}) payOrder({}) 不存在，请进行处理！]\", order.getId(), payOrderId) "
                "(L347, 源码 yudao-cloud/yudao-module-mall/yudao-module-trade-server/src/main/java/cn/iocoder/yudao/module/trade/service/order/TradeOrderUpdateServiceImpl.java)。"
                "触发条件: 交易订单支付回调时, 订单处于待支付状态, 校验支付订单时发现 payOrderId 在支付模块中不存在 (支付单不存在异常分支)。",
        "api": "POST http://localhost:9212/app-api/trade/order/update-paid "
               "(body: {\"merchantOrderId\":\"<订单id>\",\"payOrderId\":\"<不存在的支付单id>\"}, @PermitAll 免登录; 注意是 app-api 前缀)。",
        "env": "目标服务容器: trace-real-yudao-cloud-trade-server (日志在此, 端口 9212)。"
               "关联服务: trace-real-yudao-cloud-pay-server (端口 9213)。"
               "数据库 ruoyi-vue-pro 的 trade_order 表 (已预置 status=0, pay_status=0 的待支付订单)。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-trade-server",
        # 日志参数是数值，用目标日志中的特异关键词判定。
        "log_match": "不存在，请进行处理！",
    },
    "yudao_mall_trade_order_pay_order_status_not_success": {
        "sink": "TradeOrderUpdateServiceImpl.validatePayOrderPaid 中的 "
                "log.error(\"[validatePayOrderPaid][order({}) payOrder({}) 未支付，请进行处理！payOrder 数据是：{}]\", order.getId(), payOrderId, JsonUtils.toJsonString(payOrder)) "
                "(L353, 源码 yudao-cloud/yudao-module-mall/yudao-module-trade-server/src/main/java/cn/iocoder/yudao/module/trade/service/order/TradeOrderUpdateServiceImpl.java)。"
                "触发条件: 交易订单支付回调时, 订单处于待支付状态, 校验支付订单时发现支付单状态不是支付成功 (未支付/退款/关闭等状态)。",
        "api": "POST http://localhost:9212/app-api/trade/order/update-paid "
               "(body: {\"merchantOrderId\":\"<订单id>\",\"payOrderId\":\"<非成功状态的支付单id>\"}, @PermitAll 免登录; 注意是 app-api 前缀)。",
        "env": "目标服务容器: trace-real-yudao-cloud-trade-server (日志在此, 端口 9212)。"
               "关联服务: trace-real-yudao-cloud-pay-server (端口 9213)。"
               "数据库 ruoyi-vue-pro 的 trade_order 表 (已预置 status=0 的待支付订单 id=200) 与 pay_order 表 (已预置 status=0 的未支付单 id=500)。"
               "库中已预置测试记录, 请直接查现有记录 trade_order id=200 与 pay_order id=500 进行回调, 无需从头创建订单。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-trade-server",
        # 日志参数是数值与 JSON 字符串，用目标日志中的特异关键词判定。
        "log_match": "未支付，请进行处理！",
    },
    "yudao_mall_trade_order_price_mismatch": {
        "sink": "TradeOrderUpdateServiceImpl.validatePayOrderPaid 中的 "
                "log.error(\"[validatePayOrderPaid][order({}) payOrder({}) 支付金额不匹配，请进行处理！order 数据是：{}，payOrder 数据是：{}]\", order.getId(), payOrderId, JsonUtils.toJsonString(order), JsonUtils.toJsonString(payOrder)) "
                "(L359, 源码 yudao-cloud/yudao-module-mall/yudao-module-trade-server/src/main/java/cn/iocoder/yudao/module/trade/service/order/TradeOrderUpdateServiceImpl.java)。"
                "触发条件: 交易订单支付回调时, 订单处于待支付状态, 校验支付订单时发现支付单支付金额与交易订单待支付金额不一致 (金额不匹配异常分支)。",
        "api": "POST http://localhost:9212/app-api/trade/order/update-paid "
               "(body: {\"merchantOrderId\":\"<待支付订单id>\",\"payOrderId\":\"<支付单id>\"}, @PermitAll 免登录; 注意是 app-api 前缀)。",
        "env": "目标服务容器: trace-real-yudao-cloud-trade-server (日志在此, 端口 9212)。"
               "关联服务: trace-real-yudao-cloud-pay-server (端口 9213)。"
               "数据库 ruoyi-vue-pro 的 trade_order 表 (已预置 status=0, pay_price=100 的待支付订单 id=200) 与 pay_order 表 (已预置 status=10, price=200 的支付单 id=501)。"
               "库中已预置测试记录, 请直接查现有记录 trade_order id=200 与 pay_order id=501 进行回调, 无需从头创建订单。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-trade-server",
        # 日志参数是数值与 JSON 字符串，用目标日志中的特异关键词判定。
        "log_match": "支付金额不匹配，请进行处理！",
    },
    "yudao_mall_trade_order_merchant_order_id_mismatch": {
        "sink": "TradeOrderUpdateServiceImpl.validatePayOrderPaid 中的 "
                "log.error(\"[validatePayOrderPaid][order({}) 支付单不匹配({})，请进行处理！payOrder 数据是：{}]\", order.getId(), payOrderId, JsonUtils.toJsonString(payOrder)) "
                "(L365, 源码 yudao-cloud/yudao-module-mall/yudao-module-trade-server/src/main/java/cn/iocoder/yudao/module/trade/service/order/TradeOrderUpdateServiceImpl.java)。"
                "触发条件: 交易订单支付回调时, 订单处于待支付状态, 校验支付订单时发现支付单关联的商户订单号 merchantOrderId 与交易订单 id 不一致 (支付单不匹配二次校验异常分支)。",
        "api": "POST http://localhost:9212/app-api/trade/order/update-paid "
               "(body: {\"merchantOrderId\":\"200\",\"payOrderId\":502}, @PermitAll 免登录; 注意是 app-api 前缀)。",
        "env": "目标服务容器: trace-real-yudao-cloud-trade-server (日志在此, 端口 9212)。"
               "数据库 ruoyi-vue-pro 的 trade_order 表 (已预置 status=0, pay_price=100 的待支付订单 id=200) 与 pay_order 表 (已预置 status=10, price=100, merchant_order_id='999' 的支付单 id=502)。"
               "库中已预置测试记录, 本任务判定采用 log_match, 请直接向 trade-server 发送 update-paid 回调 (使用现有已支付单 502, 无需创建新订单或注入非数值字符)。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-trade-server",
        # 日志参数是数值与 JSON 字符串，用类与行号特异匹配，与 L295 区分。
        "log_match": "TradeOrderUpdateServiceImpl:365",
    },
    # ── small-swarm (mall-swarm mall-demo, 9215) ──
    "small_swarm_brand_create": {
        "sink": "DemoController.createBrand 中的 "
                "LOGGER.debug(\"createBrand success:{}\", pmsBrand) "
                "(L45, 源码 mall-swarm/mall-demo/src/main/java/com/macro/mall/demo/controller/DemoController.java)。"
                "触发条件: POST 创建品牌成功 (count==1 分支)。",
        "api": "POST http://localhost:9215/brand/create "
               "(body 需含 name/firstLetter/sort/showStatus/factoryStatus/brandStory/bigPic/pic/logo, factoryStatus 只能 0/1)。",
        "env": "目标服务容器: trace-real-mall-swarm-mall-demo (日志在此, 端口 9215)。"
               "数据库 mall 库 pms_brand 表。controller 日志级别已开 debug。",
        "hint_level": "hint",
        "container": "trace-real-mall-swarm-mall-demo",
        # controller debug 日志, 用目标日志前缀精确判定。
        "log_match": "createBrand success",
    },
    "small_swarm_brand_update": {
        "sink": "DemoController.updateBrand 中的 "
                "LOGGER.debug(\"updateBrand success:{}\", pmsBrandDto) "
                "(L61, 源码 mall-swarm/mall-demo/src/main/java/com/macro/mall/demo/controller/DemoController.java)。"
                "触发条件: POST 更新一个已存在的品牌成功 (count==1)。",
        "api": "POST http://localhost:9215/brand/update/{id} "
               "(id 用已存在品牌; body 同 create, 需 name/firstLetter/sort/showStatus/factoryStatus/logo 等)。",
        "env": "目标服务容器: trace-real-mall-swarm-mall-demo (端口 9215)。"
               "数据库 mall 库 pms_brand 表 (已有若干品牌, id 可查)。",
        "hint_level": "hint",
        "container": "trace-real-mall-swarm-mall-demo",
        "log_match": "updateBrand success",
    },
    "small_swarm_brand_update_failed": {
        "sink": "DemoController.updateBrand 中的 "
                "LOGGER.debug(\"updateBrand failed:{}\", pmsBrandDto) "
                "(L64, 源码 mall-swarm/mall-demo/src/main/java/com/macro/mall/demo/controller/DemoController.java)。"
                "触发条件: POST 更新一个不存在的品牌 (count!=1 分支)。",
        "api": "POST http://localhost:9215/brand/update/{id} "
               "(id 需在 pms_brand 表中不存在; body 需含 name/firstLetter/sort/showStatus/factoryStatus 等)。",
        "env": "目标服务容器: trace-real-mall-swarm-mall-demo (端口 9215)。"
               "数据库 mall 库 pms_brand 表。",
        "hint_level": "hint",
        "container": "trace-real-mall-swarm-mall-demo",
        "log_match": "updateBrand failed",
    },
    "small_swarm_brand_delete": {
        "sink": "DemoController.deleteBrand 中的 "
                "LOGGER.debug(\"deleteBrand success :id={}\", id) "
                "(L75, 源码 mall-swarm/mall-demo/src/main/java/com/macro/mall/demo/controller/DemoController.java)。"
                "触发条件: DELETE 一个已存在的品牌成功 (count==1)。注意接口是 GET 方法!",
        "api": "GET http://localhost:9215/brand/delete/{id} (GET 方法删除; id 需已存在, 可先 create 一个)。",
        "env": "目标服务容器: trace-real-mall-swarm-mall-demo (端口 9215)。"
               "数据库 mall 库 pms_brand 表。",
        "hint_level": "hint",
        "container": "trace-real-mall-swarm-mall-demo",
        "log_match": "deleteBrand success",
    },
    "small_swarm_brand_delete_failed": {
        "sink": "DemoController.deleteBrand 中的 "
                "LOGGER.debug(\"deleteBrand failed :id={}\", id) "
                "(L78, 源码 mall-swarm/mall-demo/src/main/java/com/macro/mall/demo/controller/DemoController.java)。"
                "触发条件: DELETE/GET 删除一个不存在的品牌 (count!=1 分支)。",
        "api": "GET http://localhost:9215/brand/delete/{id} (GET 方法删除; id 需在 pms_brand 表中不存在)。",
        "env": "目标服务容器: trace-real-mall-swarm-mall-demo (端口 9215)。"
               "数据库 mall 库 pms_brand 表。",
        "hint_level": "hint",
        "container": "trace-real-mall-swarm-mall-demo",
        "log_match": "deleteBrand failed",
    },
    # ── lamp-cloud (lamp-oauth-server, 9216) ──
    "lamp_oauth_login_success": {
        "sink": "AbstractTokenGranter.buildResult 中的 "
                "log.info(\"用户：{}  {} 登录成功\", defUser.getUsername(), defUser.getNickName()) "
                "(L425, 源码 lamp-cloud/lamp-oauth/lamp-oauth-biz/src/main/java/top/tangyh/lamp/oauth/granter/AbstractTokenGranter.java)。"
                "触发条件: 使用有效租户/客户端和密码登录成功。",
        "api": "POST http://localhost:9216/anyTenant/login (JSON body: {\"grantType\":\"PASSWORD\",\"username\":\"superAdmin\",\"password\":\"123456\"})",
        "env": "目标服务容器: trace-real-lamp-cloud-lamp-oauth-server (日志在此, 端口 9216)。"
               "请求头必须包含 Authorization: bGFtcF93ZWJfcHJvOmxhbXBfd2ViX3Byb19zZWNyZXQ= (lamp 自定义凭证, 纯 base64, 无 Basic 前缀)。"
               "数据库: trace-real-mysql, 库名 lamp_none, 用户表 def_user。",
        "hint_level": "hint",
        "container": "trace-real-lamp-cloud-lamp-oauth-server",
        "log_match": "登录成功",
    },
    "lamp_oauth_password_error_locked": {
        "sink": "PasswordTokenGranter.checkUserPassword 中的 "
                "log.info(\"密码最后一次输错后，解锁时间: {}\", passwordErrorLockExpireTime) "
                "(L122, 源码 lamp-cloud/lamp-oauth/lamp-oauth-biz/src/main/java/top/tangyh/lamp/oauth/granter/PasswordTokenGranter.java)。"
                "触发条件: 登录时所请求的用户在数据库中的密码连续输错次数达到最大限制锁定状态。",
        "api": "POST http://localhost:9216/anyTenant/login (JSON body: {\"grantType\":\"PASSWORD\",\"username\":\"...\",\"password\":\"...\"})",
        "env": "目标服务容器: trace-real-lamp-cloud-lamp-oauth-server (日志在此, 端口 9216)。"
               "请求头必须包含 Authorization: bGFtcF93ZWJfcHJvOmxhbXBfd2ViX3Byb19zZWNyZXQ= (lamp 自定义凭证, 纯 base64, 无 Basic 前缀)。"
               "数据库: trace-real-mysql, 库名 lamp_none, 用户表 def_user。缓存: trace-real-redis (端口 6379)。",
        "hint_level": "hint",
        "container": "trace-real-lamp-cloud-lamp-oauth-server",
        "log_match": "密码最后一次输错后，解锁时间:",
    },
    "lamp_oauth_refresh_token": {
        "sink": "RefreshTokenGranter.refresh 中的 "
                "log.info(\"token={},obj={}\", refreshToken, obj) "
                "(L58, 源码 lamp-cloud/lamp-oauth/lamp-oauth-biz/src/main/java/top/tangyh/lamp/oauth/granter/RefreshTokenGranter.java)。"
                "触发条件: 调用刷新 token 端点，传入 refreshToken 参数。",
        "api": "POST http://localhost:9216/anyTenant/refresh?refreshToken={refreshToken}",
        "env": "目标服务容器: trace-real-lamp-cloud-lamp-oauth-server (日志在此, 端口 9216)。"
               "请求头必须包含 Authorization: bGFtcF93ZWJfcHJvOmxhbXBfd2ViX3Byb19zZWNyZXQ= (lamp 自定义凭证, 纯 base64, 无 Basic 前缀)。",
        "hint_level": "hint",
        "container": "trace-real-lamp-cloud-lamp-oauth-server",
        "log_match": "RefreshTokenGranter  : token=",
    },
    "lamp_oauth_user_status": {
        "sink": "AbstractTokenGranter.getEmployee 中的 "
                "log.info(\"userStatus={}, userId={}, employeeId={}\", userStatus, userId, employeeId) "
                "(L284, 源码 lamp-cloud/lamp-oauth/lamp-oauth-biz/src/main/java/top/tangyh/lamp/oauth/granter/AbstractTokenGranter.java)。"
                "触发条件: 用户登录成功时加载员工信息并记录日志。",
        "api": "POST http://localhost:9216/anyTenant/login (JSON body: {\"grantType\":\"PASSWORD\",\"username\":\"superAdmin\",\"password\":\"123456\"})",
        "env": "目标服务容器: trace-real-lamp-cloud-lamp-oauth-server (日志在此, 端口 9216)。"
               "请求头必须包含 Authorization: bGFtcF93ZWJfcHJvOmxhbXBfd2ViX3Byb19zZWNyZXQ= (lamp 自定义凭证, 纯 base64, 无 Basic 前缀)。"
               "数据库: trace-real-mysql, 库名 lamp_none, 用户表 def_user (已预置 superAdmin / 123456)。",
        "hint_level": "hint",
        "container": "trace-real-lamp-cloud-lamp-oauth-server",
        "log_match": "userStatus=",
    },
    "lamp_oauth_password_error_num": {
        "sink": "PasswordTokenGranter.checkUserPassword 中的 "
                "log.info(\"[{}][{}], 输错密码次数：{}, 最大限制次数:{}\", user.getNickName(), user.getId(), passwordErrorNum, maxPasswordErrorNum) "
                "(L114, 源码 lamp-cloud/lamp-oauth/lamp-oauth-biz/src/main/java/top/tangyh/lamp/oauth/granter/PasswordTokenGranter.java)。"
                "触发条件: 登录时所请求的用户在数据库中的密码连续输错次数达到最大限制锁定状态。",
        "api": "POST http://localhost:9216/anyTenant/login (JSON body: {\"grantType\":\"PASSWORD\",\"username\":\"superAdmin\",\"password\":\"...\"})",
        "env": "目标服务容器: trace-real-lamp-cloud-lamp-oauth-server (日志在此, 端口 9216)。"
               "请求头必须包含 Authorization: bGFtcF93ZWJfcHJvOmxhbXBfd2ViX3Byb19zZWNyZXQ= (lamp 自定义凭证, 纯 base64, 无 Basic 前缀)。"
               "数据库: trace-real-mysql, 库名 lamp_none, 用户表 def_user。已预置 superAdmin 账号密码连续输错达到 10 次上限并被锁定。"
               "本任务判定采用 log_match, 请直接向 /anyTenant/login 发送登录请求 (参数 grantType=PASSWORD, username=superAdmin, password 任意), 即可触发锁定日志, 无需查询源码或探索其他接口。",
        "hint_level": "hint",
        "container": "trace-real-lamp-cloud-lamp-oauth-server",
        "log_match": "输错密码次数：",
    },
    # ── apollo (apollo-adminservice, 8080) ──
    "apollo_delete_app": {
        "sink": "AdminService.deleteApp 中的 "
                "logger.info(\"{} is deleting App:{}\", operator, appId) "
                "(L68, 源码 examples/Apollo/apollo-biz/src/main/java/com/ctrip/framework/apollo/biz/service/AdminService.java)。"
                "触发条件: 删除一个已存在的 App (AppId 在数据库中存在)。",
        "api": "DELETE http://localhost:8080/apps/{appId}?operator={operator} (需携带 operator 查询参数)",
        "env": "目标服务容器: trace-real-apollo-apollo-adminservice (日志在此, 端口 8080)。"
               "数据库: trace-real-mysql, 库名 apolloconfigdb, app 表 (已有预置应用记录, 可直接查表或先 POST /apps 创建)。",
        "hint_level": "hint",
        "container": "trace-real-apollo-apollo-adminservice",
        "log_match": "is deleting App:",
    },
    "apollo_delete_app_namespace": {
        "sink": "AppNamespaceService.deleteAppNamespace 中的 "
                "logger.info(\"{} is deleting AppNamespace, appId: {}, namespace: {}\", operator, appId, namespaceName) "
                "(L188, 源码 examples/Apollo/apollo-biz/src/main/java/com/ctrip/framework/apollo/biz/service/AppNamespaceService.java)。"
                "触发条件: 删除一个已存在的 AppNamespace 应用命名空间记录。",
        "api": "DELETE http://localhost:8080/apps/{appId}/appnamespaces/{namespaceName}?operator={operator} (需携带 operator 查询参数)",
        "env": "目标服务容器: trace-real-apollo-apollo-adminservice (日志在此, 端口 8080)。"
               "数据库: trace-real-mysql, 库名 ApolloConfigDB, 表 AppNamespace (已有预置命名空间记录，可直接查表或先 POST /apps/{appId}/appnamespaces 创建)。",
        "hint_level": "hint",
        "container": "trace-real-apollo-apollo-adminservice",
        "log_match": "is deleting AppNamespace, appId:",
    },
    # ── mogu-blog (mogu-admin, 8080) ──
    "mogu_admin_login_password_error": {
        "sink": "LoginRestApi.login 中的 "
                "log.error(\"管理员密码错误\") "
                "(L120, 源码 examples/MoGuBlog/mogu_admin/src/main/java/com/moxi/mogublog/admin/restapi/LoginRestApi.java)。"
                "触发条件: 使用已存在的管理员账号登录，但输入错误的密码。",
        "api": "POST http://localhost:8080/auth/login (参数: username=<管理员账号>, password=<不匹配的错误密码>，支持 url query 或 form 表单)",
        "env": "目标服务容器: trace-real-mogublog-mogu-admin (日志在此, 端口 8080)。"
               "数据库: trace-real-mysql, 库名 mogu_blog, 管理员表 t_admin (已存在管理员账号，可通过 query_db 查看)。",
        "hint_level": "hint",
        "container": "trace-real-mogublog-mogu-admin",
        "log_match": "管理员密码错误",
    },
    "mogu_admin_authority_verify_success": {
        "sink": "AuthorityVerifyAspect.doAround 中的 "
                "log.info(\"用户拥有操作权限，访问的路径: {}，拥有的权限接口：{}\", url, visitMap.get(url)) "
                "(L119, 源码 examples/MoGuBlog/mogu_admin/src/main/java/com/moxi/mogublog/admin/annotion/AuthorityVerify/AuthorityVerifyAspect.java)。"
                "触发条件: 使用已分配对应操作权限的管理员账号完成登录认证，携带合法 Token 访问受 @AuthorityVerify 注解保护的后台端点 (如 /blogSort/getList 等)。"
                "注意: 本任务日志参数 url 必须精确匹配权限系统注册的标准路径 (如 /blogSort/getList)，切勿在 URL 路径后拼接分号或多余字符 (Spring Security 防火墙会拦截含分号的请求路径)；harness 依据 log_match 前缀自动判定命中，无需向 URL 路径注入标记。",
        "api": "POST http://localhost:8080/auth/login 换取登录 Token，直接将响应中的完整 token 填入 Authorization 请求头，请求受保护端点 (如 POST http://localhost:8080/blogSort/getList，JSON Body 包含分页参数如 {\"currentPage\": 1, \"pageSize\": 10})",
        "env": "目标服务容器: trace-real-mogublog-mogu-admin (日志在此, 端口 8080)。"
               "数据库: trace-real-mysql, 库名 mogu_blog, 管理员表 t_admin, 角色表 t_role, 菜单表 t_category_menu (项目初始管理员账号密码在项目根目录 README.md 有明确说明)。",
        "hint_level": "hint",
        "container": "trace-real-mogublog-mogu-admin",
        "log_match": "用户拥有操作权限，访问的路径:",
    },
    # ── novel-cloud (novel-book-service, 8080) ──
    "novel_jwt_parse_failed": {
        "sink": "JwtUtils.parseToken 中的 "
                "log.warn(\"JWT解析失败:{}\", token) "
                "(L72, 源码 examples/novel-cloud/novel-core/novel-common/src/main/java/io/github/xxyopen/novel/common/auth/JwtUtils.java)。"
                "触发条件: 请求经过 TokenParseInterceptor 拦截的接口时，请求头携带无法合法解析的 Authorization token。",
        "api": "GET http://localhost:8080/api/front/book/content/{chapterId} (需携带 Authorization 请求头)",
        "env": "目标服务容器: trace-real-novel-cloud-novel-book-service (日志在此, 端口 8080)。"
               "数据库: trace-real-mysql, 库名 novel。",
        "hint_level": "hint",
        "container": "trace-real-novel-cloud-novel-book-service",
        "log_match": "JWT解析失败:",
    },
    # ── RuoYi-Cloud (ruoyi-auth, 8080) ──
    "ruoyi_auth_token_parse_failed": {
        "sink": "TokenService.getLoginUser 中的 "
                "log.error(\"获取用户信息异常'{}'\", e.getMessage()) "
                "(L113, 源码 examples/RuoYi-Cloud/ruoyi-common/ruoyi-common-security/src/main/java/com/ruoyi/common/security/service/TokenService.java)。"
                "触发条件: 请求需要解析用户身份的接口时，请求头 Authorization 携带无法合法解析的 Token。",
        "api": "POST http://localhost:8080/refresh (需在请求头 Authorization 携带 Token)",
        "env": "目标服务容器: trace-real-ruoyi-cloud-ruoyi-auth (日志在此, 端口 8080)。"
               "数据库: trace-real-mysql, 库名 ry-cloud。",
        "hint_level": "hint",
        "container": "trace-real-ruoyi-cloud-ruoyi-auth",
        "log_match": "获取用户信息异常",
    },
    # ── SpringBlade (blade-auth, 8080) ──
    "springblade_login_failed": {
        "sink": "PasswordTokenGranter.grant 中的 "
                "log.error(\"用户登录失败, 账号:{}, IP:{}\", account, WebUtil.getIP()) "
                "(L82, 源码 examples/SpringBlade/blade-auth/src/main/java/org/springblade/auth/granter/PasswordTokenGranter.java)。"
                "触发条件: 传入不存在的账号或错误密码导致登录失败。",
        "api": "POST http://localhost:8080/token (表单参数 tenantId, account, password, grantType=password)",
        "env": "目标服务容器: trace-real-springblade-blade-auth (8080) 与 trace-real-springblade-blade-system (8106)。",
        "hint_level": "hint",
        "container": "trace-real-springblade-blade-auth",
        "log_match": "用户登录失败",
        # 已知静态/框架语义不可达问题:
        # 下游 blade-system 配置了 BladeBeanSerializerModifier 将 null 对象强制序列化为 {}，
        # 经 Feign 反序列化后 userInfo.getUser() 得到非空空对象 new User()，
        # 导致 PasswordTokenGranter 的 if (userInfo == null || userInfo.getUser() == null) 恒为 false；
        # 若下游离线则直接抛 Connection refused 异常转化为 500 跳过 fallback，因此该日志分支静态/动态皆不可达。
        "static_unreachable_note": "BladeBeanSerializerModifier 将 null 对象序列化为 {}, 经 Feign 反序列化后 userInfo.getUser() 永远不为 null; 下游离线时抛 500 异常, L82 静态/动态不可达",
    },
    # ── zlt-microservices-platform (zlt-uaa, 8080) ──
    "zlt_sms_validate_code_not_expired": {
        "sink": "ValidateCodeServiceImpl.sendSmsCode 中的 "
                "log.error(\"用户:{}验证码未失效{}\", mobile, tempCode) "
                "(L62, 源码 examples/zlt-microservices-platform/zlt-uaa/src/main/java/com/central/oauth/service/impl/ValidateCodeServiceImpl.java)。"
                "触发条件: 请求发送短信验证码时，该手机号对应的验证码在缓存中未失效（已存在未过期的验证码）。",
        "api": "GET http://localhost:8080/validata/smsCode/{mobile}",
        "env": "目标服务容器: trace-real-zlt-microservices-platform-zlt-uaa (日志在此, 端口 8080)。"
               "数据库: trace-real-mysql, 库名 oauth-center。缓存: trace-real-redis (端口 6379)。",
        "hint_level": "hint",
        "container": "trace-real-zlt-microservices-platform-zlt-uaa",
        "log_match": "验证码未失效",
    },
    # ── youlai-mall (mall-oms, 8803) ──
    "youlai_order_submit_params": {
        "sink": "OrderServiceImpl.submitOrder 中的 "
                "log.info(\"订单提交参数:{}\", JSONUtil.toJsonStr(submitForm)) "
                "(L176, 源码 examples/youlai-mall/mall-oms/oms-boot/src/main/java/com/youlai/mall/oms/service/app/impl/OrderServiceImpl.java)。"
                "触发条件: 调用订单提交接口，提交符合校验规则的订单表单数据。",
        "api": "POST http://localhost:8803/app-api/v1/orders/submit (JSON 请求体: 提交符合 OrderSubmitForm 校验的订单数据)",
        "env": "目标服务容器: trace-real-youlai-mall-oms (日志在此, 端口 8803)。"
               "数据库: trace-real-mysql, 库名 mall_oms。缓存: trace-real-redis (端口 6379)。",
        "hint_level": "hint",
        "container": "trace-real-youlai-mall-oms",
        "log_match": "订单提交参数:",
    },
    # ── mall4cloud (mall4cloud-auth, 8080) ──
    "mall4cloud_token_decrypt_error": {
        "sink": "TokenStore.decryptToken 中的 "
                "logger.error(e.getMessage()) "
                "(L264, 源码 examples/mall4cloud/mall4cloud-auth/src/main/java/com/mall4j/cloud/auth/manager/TokenStore.java)。"
                "触发条件: 请求刷新 Token 时，传入无法合法解密的 refreshToken 触发解密异常处理。",
        "api": "POST http://localhost:8080/ua/token/refresh (JSON 请求体: {\"refreshToken\":\"<非法或无法解密的token>\"})",
        "env": "目标服务容器: trace-real-mall4cloud-mall4cloud-auth (日志在此, 端口 8080)。"
               "数据库: trace-real-mysql, 库名 mall4cloud_auth。缓存: trace-real-redis (端口 6379)。",
        "hint_level": "hint",
        "container": "trace-real-mall4cloud-mall4cloud-auth",
        "log_match": "c.mall4j.cloud.auth.manager.TokenStore",
    },
    # ── PiggyMetrics (account-service, 8080) ──
    "piggymetrics_token_fetch_user_details_failed": {
        "sink": "CustomUserInfoTokenServices.getMap 中的 "
                "this.logger.info(\"Could not fetch user details: \" + ex.getClass() + \", \" + ex.getMessage()) "
                "(L132, 源码 examples/PiggyMetrics/account-service/src/main/java/com/piggymetrics/account/service/security/CustomUserInfoTokenServices.java)。"
                "触发条件: 访问受 OAuth2 保护的端点并携带 Token 凭证时，触发远程用户信息获取或解析异常分支。",
        "api": "GET http://localhost:8080/current (请求头: Authorization: Bearer <token>) 或其他受保护业务端点",
        "env": "目标服务容器: trace-real-piggymetrics-account-service (日志在此, 端口 8080)。"
               "数据库: trace-real-mongo (端口 27017)。缓存: trace-real-redis (端口 6379)。",
        "hint_level": "hint",
        "container": "trace-real-piggymetrics-account-service",
        "log_match": "Could not fetch user details:",
    },
    # ── Cloud-Platform (ace-admin, 8080) ──
    "cloud_platform_auth_require_logging": {
        "sink": "AuthController.createAuthenticationToken 中的 "
                "log.info(authenticationRequest.getUsername() + \" require logging...\") "
                "(L40, 源码 examples/Cloud-Platform/ace-modules/ace-admin/src/main/java/com/github/wxiaoqi/security/modules/auth/controller/AuthController.java)。"
                "触发条件: 发起获取 JWT Token 的身份认证请求，在校验验证码与用户凭据前记录请求用户名日志。",
        "api": "POST http://localhost:8080/jwt/token (JSON 请求体: {\"username\":\"...\",\"password\":\"...\"})",
        "env": "目标服务容器: trace-real-cloud-platform-ace-admin (日志在此, 端口 8080)。"
               "数据库: trace-real-mysql, 库名 cloud_admin_v1。缓存: trace-real-redis (端口 6379)。",
        "hint_level": "hint",
        "container": "trace-real-cloud-platform-ace-admin",
        "log_match": " require logging...",
    },
    # ── java-microservice (8080) ──
    "javams_order_evaluate_risk_high": {
        "sink": "OrderService.evaluateRisk 中的 "
                "log.error(\"OrderService.evaluateRisk: VIP大额APP端高风险订单, orderId={}\", orderId) "
                "(L77, 源码 examples/java-microservice/src/main/java/com/example/microservice/service/OrderService.java)。"
                "触发条件: 订单风控评估接口接收到符合高风险策略组合约束的订单评估参数。",
        "api": "POST http://localhost:8080/api/orders/risk-eval (JSON 请求体)",
        "env": "目标服务容器: trace-real-java-microservice (日志在此, 端口 8080)。"
               "数据库: trace-real-mysql, 库名 microservice。缓存: trace-real-redis (端口 6379)。",
        "hint_level": "hint",
        "container": "trace-real-java-microservice",
        "log_match": "VIP大额APP端高风险订单",
    },
}


def build_task_desc(t: dict) -> str:
    """从任务字段拼装任务描述。hint_level 控制信息量:
    - none: 只给 sink 描述 (连 API 入口都不给, 纯黑盒对照)
    - hint: sink + api + env (标准灰盒)
    - full: 额外给前置条件提示 (调试用)
    """
    parts = [f"目标 sink: {t['sink']}"]
    if t.get("hint_level") in ("hint", "full"):
        parts.append(f"API 入口: {t['api']}")
        parts.append(f"运行环境: {t['env']}")
    if t.get("hint_level") == "full" and t.get("precondition_hint"):
        parts.append(f"前置条件提示: {t['precondition_hint']}")
    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sink", default="ruoyi_plus_menu_same_level", help="sink 任务名")
    parser.add_argument("--list", action="store_true", help="列出可用任务")
    parser.add_argument("--max-turns", type=int, default=15, help="API 轮次上限")
    parser.add_argument("--max-tools", type=int, default=80, help="工具调用预算")
    parser.add_argument("--hint-level", default="", choices=["", "none", "hint", "full"],
                        help="覆盖任务的提示级别 (对照实验用)")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--output", default="", help="结果保存路径")
    args = parser.parse_args()

    if args.list:
        for k, v in SINK_TASKS.items():
            print(f"  {k}: {v['sink'][:80]}...")
        return

    task = SINK_TASKS.get(args.sink)
    if not task:
        print(f"未知任务: {args.sink}, 用 --list 查看")
        return
    if args.hint_level:
        task = {**task, "hint_level": args.hint_level}

    print(f"═══ SinkAgent: {args.sink} (hint={task['hint_level']}) ═══")
    print(f"模型: {os.environ.get('LLM_MODEL')}, max_turns: {args.max_turns}, max_tools: {args.max_tools}")
    agent = SinkAgent(verbose=args.verbose, max_tool_calls=args.max_tools)
    result = agent.run(build_task_desc(task), container=task.get("container", ""),
                       max_turns=args.max_turns, log_match=task.get("log_match", ""))

    print("\n═══ 结果 ═══")
    print(f"命中(harness判定): {result['reached']}")
    print(f"轮次: {result['turns']}, 工具调用: {result['tool_calls']}, 耗时: {result['elapsed']:.1f}s")

    if args.output:
        with open(args.output, "w") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"已保存: {args.output}")


if __name__ == "__main__":
    main()
