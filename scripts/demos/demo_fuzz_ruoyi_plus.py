"""
demo_fuzz_ruoyi_plus.py — RuoYi-Cloud-Plus SysMenu 路由冲突 Log Injection Fuzz

目标: RuoYi-Cloud-Plus ruoyi-system 的 POST /menu (新增菜单)
目标 Sink: SysMenuServiceImpl.checkRouteConfigUnique() 中 3 条 log.warn

3 个 sink 的 {} 占位符包含用户可控参数:
  Sink1: path==dbPath && parentId==dbParentId → log.warn("同级下已存在相同路由路径 '{}'", dbPath)
  Sink2: path==dbPath && parentId==0 && dbParentId==0 → log.warn("根目录下路由 '{}' 必须唯一", path)
  Sink3: routeName==dbRouteName && menuType==dbMenuType → log.warn("路由名称 '{}' 需全局唯一", routeName)

setup 阶段预先创建种子菜单 (path=sink_attacked), 让 LLM 通过偏差反馈推理出正确参数组合.

运行前提:
  cd examples-yml/RuoYi-Cloud-Plus && docker compose -f compose.real.yaml up -d

使用方式:
  source .env && export LLM_BASE_URL LLM_API_KEY LLM_MODEL
  uv run python demo_fuzz_ruoyi_plus.py --max-attempts 8 -v
  uv run python demo_fuzz_ruoyi_plus.py --mock
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import urllib.error
import urllib.request

from demo_fuzz import execute_with_trace, check_container_log_after_line, get_container_log_line_count
from expected_path import APIEntry, ExpectedPath, PathNode, PathSource
from fuzzer import Fuzzer
from llm import LLM, Message
from parameter import HttpParameter
from pipeline import Pipeline
from sink import Sink, SinkType
from trace import Trace

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# RuoYi-Cloud-Plus 手工标注
# ═══════════════════════════════════════════════════════════════════════════════

AUTH_URL = "http://localhost:8080"
SYSTEM_URL = "http://localhost:9201"
CONTAINER_NAME = "trace-real-ruoyi-cloud-plus-ruoyi-system"
ATTACK_MARKER = "sink_attacked"

LOGIN_BODY = {
    "clientId": "e5cd7e4891bf95d1d19206ce24a7b32e",
    "grantType": "password",
    "tenantId": "000000",
    "username": "admin",
    "password": "admin123",
}

# ── API 入口 ──────────────────────────────────────────────────────────────────
API_ADD_MENU = APIEntry(
    class_name="org.dromara.system.controller.system.SysMenuController",
    method="add",
    http_method="POST",
    http_path="/menu",
    src_file="SysMenuController.java",
    line_number=135,
)

# ── Sink 点 ──────────────────────────────────────────────────────────────────

# Sink1: 同级路由冲突 — dbPath (= 攻击标记) 被打印
SINK_SAME_LEVEL_ROUTE = Sink(
    class_name="org.dromara.system.service.impl.SysMenuServiceImpl",
    method="checkRouteConfigUnique",
    line_number=386,
    src_file="SysMenuServiceImpl.java",
    sink_type=SinkType.LOG_ERROR,
    log_message="[同级路由冲突] 同级下已存在相同路由路径 '{}'，冲突菜单：{}",
    log_api="log.warn",
    tainted_params=["path", "parentId", "menuType", "menuName"],
)

# Sink2: 根目录路由冲突 — path (用户直接输入) 被打印
SINK_ROOT_ROUTE = Sink(
    class_name="org.dromara.system.service.impl.SysMenuServiceImpl",
    method="checkRouteConfigUnique",
    line_number=391,
    src_file="SysMenuServiceImpl.java",
    sink_type=SinkType.LOG_ERROR,
    log_message="[根目录路由冲突] 根目录下路由 '{}' 必须唯一，已被菜单 '{}' 占用",
    log_api="log.warn",
    tainted_params=["path", "parentId", "menuType", "menuName"],
)

# Sink3: 路由名称冲突 — routeName (用户直接输入) 被打印
SINK_ROUTE_NAME = Sink(
    class_name="org.dromara.system.service.impl.SysMenuServiceImpl",
    method="checkRouteConfigUnique",
    line_number=395,
    src_file="SysMenuServiceImpl.java",
    sink_type=SinkType.LOG_ERROR,
    log_message="[路由名称冲突] 路由名称 '{}' 需全局唯一，已被菜单 '{}' 使用",
    log_api="log.warn",
    tainted_params=["routeName", "menuType", "menuName"],
)

# ── 预期路径 ──────────────────────────────────────────────────────────────────
PATH_ADD_MENU = ExpectedPath(
    api_entry=API_ADD_MENU,
    log_sink=None,  # type: ignore
    nodes=[
        PathNode(class_name="org.dromara.system.controller.system.SysMenuController",
                 method="add", depth=0),
        PathNode(class_name="org.dromara.system.service.impl.SysMenuServiceImpl",
                 method="checkRouteConfigUnique", depth=1),
    ],
    source=PathSource.TAINT,
    confidence=0.9,
)

# ── Fuzz 目标清单 ─────────────────────────────────────────────────────────────
FUZZ_TARGETS = [
    (API_ADD_MENU, SINK_SAME_LEVEL_ROUTE, PATH_ADD_MENU),
    (API_ADD_MENU, SINK_ROOT_ROUTE, PATH_ADD_MENU),
    (API_ADD_MENU, SINK_ROUTE_NAME, PATH_ADD_MENU),
]


# ═══════════════════════════════════════════════════════════════════════════════
# 认证 + 执行器
# ═══════════════════════════════════════════════════════════════════════════════

_auth_token: str = ""


def login() -> str:
    """登录 RuoYi-Cloud-Plus 并返回 access_token。"""
    req = urllib.request.Request(
        f"{AUTH_URL}/login",
        method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps(LOGIN_BODY).encode(),
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    if data.get("code") != 200:
        raise RuntimeError(f"Login failed: {data}")
    return data["data"]["access_token"]


def execute_with_auth(param: HttpParameter) -> Trace:
    """包装 execute_with_trace，注入 Sa-Token 认证。"""
    global _auth_token
    param.headers["Authorization"] = f"Bearer {_auth_token}"
    # menu API 在 system 服务 (9201)，重写 URL
    if param.url.startswith(SYSTEM_URL) or "/menu" in param.url:
        pass  # URL 已正确
    trace = execute_with_trace(param)

    resp = getattr(trace, "response_body", "")
    if "认证失败" in resp or "未能读取到有效Token" in resp:
        logger.info("  Token 过期，重新登录...")
        _auth_token = login()
        param.headers["Authorization"] = f"Bearer {_auth_token}"
        trace = execute_with_trace(param)
    return trace


def setup_seed_menus() -> None:
    """创建种子菜单，为路由冲突 fuzz 提供 DB 中的匹配目标。"""
    seeds = [
        # 根目录种子: parentId=0, menuType=M, path=sink_attacked
        {"menuName": "fuzz_seed_root", "parentId": 0, "orderNum": 90,
         "path": ATTACK_MARKER, "menuType": "M", "visible": "0",
         "status": "0", "isFrame": "1", "isCache": "0"},
        # 子目录种子: parentId=1 (系统管理下), menuType=C, path=sink_attacked
        {"menuName": "fuzz_seed_child", "parentId": 1, "orderNum": 91,
         "path": ATTACK_MARKER, "menuType": "C", "visible": "0",
         "status": "0", "isFrame": "1", "isCache": "0"},
    ]
    for seed in seeds:
        req = urllib.request.Request(
            f"{SYSTEM_URL}/menu",
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {_auth_token}",
            },
            data=json.dumps(seed).encode(),
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            if data.get("code") == 200:
                logger.info(f"  种子菜单创建成功: {seed['menuName']} (parentId={seed['parentId']})")
            else:
                logger.info(f"  种子菜单已存在或失败: {seed['menuName']} → {data.get('msg', '')[:60]}")
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            logger.info(f"  种子菜单创建异常: {seed['menuName']} → {body[:80]}")


# ═══════════════════════════════════════════════════════════════════════════════
# Mock LLM
# ═══════════════════════════════════════════════════════════════════════════════

class RuoYiPlusMockLLM(LLM):
    """
    Mock LLM: 第1轮盲猜错误参数 → 第2轮根据偏差反馈+源码推理出正确组合。
    """

    def __init__(self):
        super().__init__(base_url="mock://", api_key="mock", model="mock")

    def chat(self, messages: list[Message]) -> str:
        system_text = " ".join(m.content for m in messages if m.role == "system")
        has_feedback = any("偏差" in m.content or "未能到达" in m.content
                          for m in messages if m.role == "user")

        if not has_feedback:
            return self._blackbox_guess()

        # 根据 system prompt 中的 sink 描述选择正确参数
        if "同级路由冲突" in system_text:
            # 子目录种子 parentId=1, 同级冲突
            return self._wrap({"menuName": "fuzz_conflict_1", "parentId": 1,
                               "orderNum": 92, "path": ATTACK_MARKER,
                               "menuType": "C", "visible": "0", "status": "0",
                               "isFrame": "1", "isCache": "0"})
        elif "根目录路由冲突" in system_text:
            # 根目录种子 parentId=0
            return self._wrap({"menuName": "fuzz_conflict_2", "parentId": 0,
                               "orderNum": 93, "path": ATTACK_MARKER,
                               "menuType": "M", "visible": "0", "status": "0",
                               "isFrame": "1", "isCache": "0"})
        elif "路由名称冲突" in system_text:
            # routeName 冲突, menuType=M 匹配根目录种子
            return self._wrap({"menuName": "fuzz_conflict_3", "parentId": 2,
                               "orderNum": 94, "path": "unique_path_3",
                               "routeName": ATTACK_MARKER,
                               "menuType": "M", "visible": "0", "status": "0",
                               "isFrame": "1", "isCache": "0"})
        return self._blackbox_guess()

    def _blackbox_guess(self) -> str:
        # 盲猜: menuType=F(按钮，会被跳过), 错误 parentId
        return self._wrap({"menuName": "test_menu", "parentId": 999,
                           "orderNum": 1, "path": "test",
                           "menuType": "F", "visible": "0", "status": "0",
                           "isFrame": "1", "isCache": "0"})

    def _wrap(self, body: dict) -> str:
        return "<think>分析路由冲突条件</think>\n<json>\n" + json.dumps({
            "method": "POST",
            "url": f"{SYSTEM_URL}/menu",
            "headers": {"Content-Type": "application/json"},
            "body": body,
        }) + "\n</json>"


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    global _auth_token, SYSTEM_URL, AUTH_URL

    parser = argparse.ArgumentParser(description="RuoYi-Cloud-Plus SysMenu 路由冲突 Log Injection Fuzz")
    parser.add_argument("--mock", action="store_true", help="使用 Mock LLM")
    parser.add_argument("--auth-url", default=AUTH_URL, help="认证服务 URL")
    parser.add_argument("--system-url", default=SYSTEM_URL, help="系统服务 URL")
    parser.add_argument("--max-attempts", type=int, default=8, help="每条路径最大尝试次数")
    parser.add_argument("--marker", default=ATTACK_MARKER, help="攻击标记字符串")
    parser.add_argument("--container", default=CONTAINER_NAME, help="目标容器名")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    AUTH_URL = args.auth_url
    SYSTEM_URL = args.system_url
    container_name = args.container

    def _check_log(marker: str, skip_lines: int) -> bool:
        return check_container_log_after_line(marker, skip_lines, container_name)

    def _get_log_line_count() -> int:
        return get_container_log_line_count(container_name)

    # LLM
    if args.mock:
        llm = RuoYiPlusMockLLM()
        logger.info("使用 MockLLM (测试模式)")
    else:
        llm = LLM()
        if not llm.api_key:
            logger.error("未设置 LLM_API_KEY，请设置或使用 --mock")
            sys.exit(1)
        logger.info(f"使用 LLM: {llm.model} @ {llm.base_url}")

    # 登录
    logger.info(f"认证服务: {AUTH_URL}, 系统服务: {SYSTEM_URL}")
    logger.info("登录 RuoYi-Cloud-Plus ...")
    try:
        _auth_token = login()
        logger.info("登录成功")
    except Exception as e:
        logger.error(f"登录失败: {e}")
        sys.exit(1)

    # 创建种子菜单
    logger.info("创建种子菜单 (fuzz 前置数据) ...")
    setup_seed_menus()

    logger.info(f"Fuzz 目标数: {len(FUZZ_TARGETS)}")
    logger.info("")

    # trace-agent 在此镜像中可能未正常工作，用日志检测代替 trace 检测
    def sink_always_reached(trace: Trace, sink: Sink) -> bool:
        return True

    # 构建 Pipeline
    fuzzer = Fuzzer(
        llm=llm,
        base_url=SYSTEM_URL,
        attack_marker=args.marker,
        source_root="examples/RuoYi-Cloud-Plus",
        verbose=args.verbose,
    )
    pipeline = Pipeline(
        fuzzer=fuzzer,
        execute_fn=execute_with_auth,
        check_log_fn=_check_log,
        get_log_line_count_fn=_get_log_line_count,
        max_attempts=args.max_attempts,
        sink_checker=sink_always_reached,
    )

    result = pipeline.run(FUZZ_TARGETS)
    logger.info(f"\n{result.summary}")
    print(f"\n{result.summary}")
    sys.exit(0 if result.reached_count > 0 else 1)


if __name__ == "__main__":
    main()
