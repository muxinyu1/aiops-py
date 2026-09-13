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
import time
import urllib.error
import urllib.request

import requests

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
    if any(b in sql.upper() for b in banned):
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
    parts = cmd_str.split()
    try:
        r = subprocess.run(["docker", "exec", container, "redis-cli", "--raw", *parts],
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
                self.tool_call_count += 1
                if self.tool_call_count > self.max_tool_calls:
                    tool_results.append({"role": "tool", "tool_call_id": tc["id"],
                                         "content": "BUDGET EXCEEDED: 工具调用预算已用尽, 请立即总结当前证据并给出结论。"})
                    continue
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
            if self.tool_call_count > self.max_tool_calls:
                # 强制结束: 不再发起新一轮 API 请求
                return False, "budget_exceeded"
            data = self._api(payload)

        msg = data["choices"][0]["message"]
        text = (msg.get("content") or "").strip()
        self.messages.append(msg)

        # 命中判定不在 agent 自述, 由 harness 外部完成 (run 中 grep 日志)
        reached = "<<SINK_REACHED>>" in text
        return reached, text

    def run(self, task_desc: str, container: str = "", max_turns: int = 15) -> dict:
        system_prompt = SYSTEM_PROMPT.replace("{task}", task_desc)
        self.messages = [{"role": "user", "content": system_prompt}]
        self._container = container
        # harness 基线: 记录运行开始时的 marker 命中数, 只统计新增命中
        self._baseline_hits = self._count_sink_hits()
        t0 = time.time()
        text = ""
        for turn_i in range(1, max_turns + 1):
            print(f"  ── turn {turn_i} ──")
            try:
                reached, text = self.turn()
            except Exception as e:
                print(f"  ⚠ API 异常: {e}")
                time.sleep(2)
                continue
            print(f"    💭 {text[:400]}")
            elapsed = time.time() - t0
            # harness 侧命中判定: 不管 agent 是否声称成功, 直接 grep 容器日志 (对比基线增量)
            if self._count_sink_hits() > self._baseline_hits:
                print(f"  ✅ harness 判定 sink 命中 (本次运行新增 marker 命中)! 耗时 {elapsed:.1f}s, 工具调用 {self.tool_call_count} 次")
                return {"reached": True, "turns": turn_i, "tool_calls": self.tool_call_count,
                        "elapsed": elapsed, "tool_log": self.tool_log, "final_text": text}
            if reached:
                print(f"  ⚠ agent 自称命中但 harness 日志检查未见新增标记, 继续观察...")
            if text == "budget_exceeded":
                break
        return {"reached": False, "turns": max_turns, "tool_calls": self.tool_call_count,
                "elapsed": time.time() - t0, "tool_log": self.tool_log, "final_text": text}

    def _count_sink_hits(self) -> int:
        """统计容器日志/日志文件中 marker 出现次数 (整个日志, 含历史)。
        宽容匹配: 应用 XSS 过滤器会剥部分尖括号, 所以 'SINK_REACHED' 字符串本体出现即算。"""
        container = getattr(self, "_container", "")
        if not container:
            return 0
        if container.endswith(".log"):
            try:
                with open(container) as f:
                    return f.read().count("SINK_REACHED")
            except OSError:
                return 0
        r = subprocess.run(["docker", "logs", container],
                           capture_output=True, text=True, timeout=60)
        return (r.stdout + r.stderr).count("SINK_REACHED")

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
                "触发条件: system_menu 表中存在某菜单, 其 parent_id 指向一个不存在(或已删除)的菜单 id, 且该菜单被 admin 用户的角色关联并可见。",
        "api": "GET http://localhost:8080/admin-api/system/auth/get-permission-info (需登录, header: Authorization: Bearer <token>, tenant-id: 1)。"
               "登录: POST http://localhost:8080/admin-api/system/auth/login, header tenant-id: 1, body {\"username\":\"admin\",\"password\":\"admin123\"}, 取 data.accessToken。",
        "env": "目标服务容器: trace-real-yudao-cloud-yudao-module-system-server (日志在此)。"
               "数据库: docker 容器 trace-real-mysql, 库名 ruoyi-vue-pro, 菜单表 system_menu (字段: id/name/parent_id/status/deleted/type), 角色菜单关联表 system_role_menu。",
        "hint_level": "hint",
        "container": "trace-real-yudao-cloud-yudao-module-system-server",
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
    result = agent.run(build_task_desc(task), container=task.get("container", ""), max_turns=args.max_turns)

    print("\n═══ 结果 ═══")
    print(f"命中(harness判定): {result['reached']}")
    print(f"轮次: {result['turns']}, 工具调用: {result['tool_calls']}, 耗时: {result['elapsed']:.1f}s")

    if args.output:
        with open(args.output, "w") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"已保存: {args.output}")


if __name__ == "__main__":
    main()
