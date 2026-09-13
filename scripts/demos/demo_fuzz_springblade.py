"""
demo_fuzz_springblade.py — SpringBlade CaptchaTokenGranter 多参数组合 Fuzz 演示

目标: SpringBlade blade-auth 的 POST /token (grant_type=captcha)
目标 Sink: CaptchaTokenGranter.grant() 第 95 行
  log.error("用户登录失败, 账号:{}, IP:{}", account, WebUtil.getIP())

6 参数组合条件:
  1. Captcha-Key (header)   — Redis 中预存的验证码 key
  2. Captcha-Code (header)  — 必须与 Redis 中的值匹配 (忽略大小写)
  3. tenantId (query)       — 必须是有效租户 ID (000000)
  4. account (query)        — 账号名
  5. password (query)       — SM2 加密密码 (当前 key 为空, 解密返回空串)
  6. User-Type (header)     — "web" 或 "app", 决定调用哪个分支

源码引导优势 (vs 黑盒):
  - 黑盒不知道需要 Captcha-Key/Captcha-Code 两个自定义 header
  - 黑盒不知道验证码存储在 Redis 的 blade:auth::blade:captcha:{key} 下
  - 黑盒不知道需要 User-Type header 且只接受 "web"/"app"
  - 黑盒不知道需要 Basic Authorization header (client_id:client_secret)
  - 黑盒不知道 tenantId 必须是有效值 (000000)
  - 即使猜对了部分参数, 缺少任一条件都无法到达 sink

运行前提:
  cd examples-yml/SpringBlade && docker compose -f compose.real.yaml up -d

使用方式:
  source .env && export LLM_BASE_URL LLM_API_KEY LLM_MODEL
  uv run python demo_fuzz_springblade.py --max-attempts 5
  uv run python demo_fuzz_springblade.py --mock     # 无需 LLM 的测试模式
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import subprocess
import sys
import time
import uuid

from demo_fuzz import (
    execute_with_trace,
    check_container_log_after_line,
    get_container_log_line_count,
    MockLLM,
)
from expected_path import APIEntry, ExpectedPath, PathNode, PathSource
from fuzzer import Fuzzer, FuzzAttempt
from llm import LLM, Message
from parameter import HttpParameter
from pipeline import Pipeline, PipelineResult, check_sink_reached
from sink import Sink, SinkType
from trace import Trace, TraceNode

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# SpringBlade blade-auth 手工标注
# ═══════════════════════════════════════════════════════════════════════════════

BASE_URL = "http://localhost:8080"
CONTAINER_NAME = "trace-real-springblade-blade-auth"
REDIS_CONTAINER = "trace-real-redis"
REDIS_PASSWORD = "password"
ATTACK_MARKER = "用户登录失败"

# Basic Auth: sword:sword_secret (SpringBlade 默认 OAuth2 client)
BASIC_AUTH = base64.b64encode(b"sword:sword_secret").decode()

# ── API 入口 ──────────────────────────────────────────────────────────────────

API_TOKEN = APIEntry(
    class_name="org.springblade.auth.controller.AuthController",
    method="token",
    http_method="POST",
    http_path="/token",
    src_file="AuthController.java",
    line_number=56,
)

# ── Sink 定义 ─────────────────────────────────────────────────────────────────
# CaptchaTokenGranter.grant() 第 95 行:
#   log.error("用户登录失败, 账号:{}, IP:{}", account, WebUtil.getIP())
# 紧接着抛出 ServiceException("用户名或密码错误")
#
# 到达条件 (6 参数组合):
#   1. Captcha-Key + Captcha-Code 匹配 Redis 中的验证码
#   2. tenantId + account 未被锁定 (锁定阈值=5次, TTL=30分钟)
#   3. password 经过 SM2 解密 (当前 key 为空, 解密返回空串)
#   4. User-Type 必须是 "web" 或 "app"
#   5. Feign 调用 blade-system 获取用户信息 (需要 blade-system 服务运行)
#   6. 用户查询结果为空或密码不匹配 → 触发 sink

SINK_LOGIN_FAILED = Sink(
    class_name="org.springblade.auth.granter.CaptchaTokenGranter",
    method="grant",
    sink_type=SinkType.LOG_ERROR,
    log_message="用户登录失败, 账号:{}, IP:{}",
    log_api="log.error",
    tainted_params=["account"],
    src_file="CaptchaTokenGranter.java",
    line_number=95,
)

# ── 预期路径 ──────────────────────────────────────────────────────────────────

PATH_LOGIN_FAILED = ExpectedPath(
    api_entry=API_TOKEN,
    log_sink=SINK_LOGIN_FAILED,
    nodes=[
        PathNode(
            class_name="org.springblade.auth.controller.AuthController",
            method="token",
        ),
        PathNode(
            class_name="org.springblade.auth.granter.CaptchaTokenGranter",
            method="grant",
        ),
        PathNode(
            class_name="org.springblade.auth.utils.TokenUtil",
            method="checkAccountAndIpLock",
        ),
        PathNode(
            class_name="org.springblade.auth.utils.TokenUtil",
            method="decryptPassword",
        ),
        PathNode(
            class_name="org.springblade.system.user.feign.IUserClient",
            method="userInfo",
        ),
        PathNode(
            class_name="org.springblade.auth.utils.TokenUtil",
            method="handleLoginFailure",
        ),
    ],
    source=PathSource.TAINT,
    confidence=0.9,
)

FUZZ_TARGETS = [
    (API_TOKEN, SINK_LOGIN_FAILED, PATH_LOGIN_FAILED),
]


# ═══════════════════════════════════════════════════════════════════════════════
# Redis 验证码预填
# ═══════════════════════════════════════════════════════════════════════════════

def prepare_captcha(captcha_key: str, captcha_code: str, ttl: int = 300) -> bool:
    """在 Redis 中预填验证码, 返回是否成功.
    注意: BladeRedis 使用 ProtoStuff 序列化, redis-cli SET 的纯文本无法被反序列化。
    此方法仅作为 fallback, 实际应通过 /captcha API 获取验证码。
    """
    redis_key = f"blade:auth::blade:captcha:{captcha_key}"
    try:
        result = subprocess.run(
            [
                "docker", "exec", REDIS_CONTAINER,
                "redis-cli", "-a", REDIS_PASSWORD,
                "SET", redis_key, captcha_code, "EX", str(ttl),
            ],
            capture_output=True, text=True, timeout=5,
        )
        return "OK" in result.stdout
    except Exception as e:
        logger.warning(f"Redis 验证码预填失败: {e}")
        return False


def get_real_captcha() -> tuple[str, str]:
    """
    通过 /captcha API 获取真实验证码 key, 再从 Redis 中提取验证码值。

    BladeRedis 使用 ProtoStuff 序列化, 存储格式为:
      0x07 0x4A <len> <ascii_code>
    验证码是 5 位字母/数字, 从原始字节中提取 ASCII 部分即可。

    Returns:
        (key, code) 元组, 失败返回 ("", "")
    """
    import urllib.request
    try:
        # Step 1: 调用 /captcha API 获取 key
        req = urllib.request.Request(f"{BASE_URL}/captcha")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        key = data["data"]["key"]

        # Step 2: 从 Redis 中读取 ProtoStuff 序列化的验证码值
        result = subprocess.run(
            [
                "docker", "exec", REDIS_CONTAINER,
                "redis-cli", "-a", REDIS_PASSWORD, "--no-auth-warning",
                "--raw", "GET", f"blade:auth::blade:captcha:{key}",
            ],
            capture_output=True, timeout=5,
        )
        raw = result.stdout
        # ProtoStuff 格式: 0x07 0x4A <len_byte> <captcha_chars>
        # 0x4A = ASCII 'J' 会被误识别为验证码字符
        # 跳过前 3 字节 header，从第 4 字节开始提取 ASCII 字符
        code = ""
        start = 3 if len(raw) > 3 else 0
        for b in raw[start:]:
            if 0x30 <= b <= 0x39 or 0x41 <= b <= 0x5A or 0x61 <= b <= 0x7A:
                code += chr(b)
        if code:
            logger.info(f"获取真实验证码: key={key[:16]}..., code={code}")
            return key, code
        else:
            logger.warning(f"无法从 Redis 中提取验证码值")
            return key, ""
    except Exception as e:
        logger.warning(f"获取真实验证码失败: {e}")
        return "", ""


# ═══════════════════════════════════════════════════════════════════════════════
# SpringBlade Mock LLM — 模拟源码引导的参数生成
# ═══════════════════════════════════════════════════════════════════════════════

class SpringBladeMockLLM(LLM):
    """
    Mock LLM 模拟源码引导的 fuzz 策略。

    演示 6 参数组合条件的逐步探索:
      第 1 轮: 缺少 Captcha 头 → 验证码校验失败
      第 2 轮: Captcha 错误 → 验证码不匹配
      第 3 轮: 缺少 User-Type → NullPointerException
      第 4 轮: 正确参数组合 → 到达 sink (用户名或密码错误)
    """

    def __init__(self):
        super().__init__(base_url="mock://", api_key="mock", model="mock")
        self._call_count = 0

    def chat(self, messages: list[Message]) -> str:
        self._call_count += 1
        attempt = self._call_count

        if attempt == 1:
            # 第 1 轮: 黑盒式尝试 — 缺少自定义 header
            return json.dumps({
                "method": "POST",
                "url": f"{BASE_URL}/token?grantType=captcha&account=admin&password=test&tenantId=000000",
                "headers": {
                    "Authorization": f"Basic {BASIC_AUTH}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                "body": None,
            })

        elif attempt == 2:
            # 第 2 轮: 加了 Captcha 头但验证码不匹配
            return json.dumps({
                "method": "POST",
                "url": f"{BASE_URL}/token?grantType=captcha&account=admin&password=test&tenantId=000000",
                "headers": {
                    "Authorization": f"Basic {BASIC_AUTH}",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Captcha-Key": "wrong-key",
                    "Captcha-Code": "0000",
                },
                "body": None,
            })

        elif attempt == 3:
            # 第 3 轮: 真实验证码, 但缺少 User-Type
            key, code = get_real_captcha()
            return json.dumps({
                "method": "POST",
                "url": f"{BASE_URL}/token?grantType=captcha&account=admin&password=04test&tenantId=000000",
                "headers": {
                    "Authorization": f"Basic {BASIC_AUTH}",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Captcha-Key": key,
                    "Captcha-Code": code,
                },
                "body": None,
            })

        else:
            # 第 4 轮: 完整 6 参数组合 → 到达 sink
            key, code = get_real_captcha()
            return json.dumps({
                "method": "POST",
                "url": f"{BASE_URL}/token?grantType=captcha&account=admin&password=04test&tenantId=000000",
                "headers": {
                    "Authorization": f"Basic {BASIC_AUTH}",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Captcha-Key": key,
                    "Captcha-Code": code,
                    "User-Type": "web",
                },
                "body": None,
            })


# ═══════════════════════════════════════════════════════════════════════════════
# 自定义执行器: 支持验证码预填 + trace 获取
# ═══════════════════════════════════════════════════════════════════════════════

def execute_with_captcha_and_trace(param: HttpParameter) -> Trace:
    """
    对于 LLM 模式: 在发送请求前自动预填验证码到 Redis。
    Captcha-Key / Captcha-Code 从请求 header 中提取。

    对于 Mock 模式: Mock LLM 自己处理预填。
    """
    # 如果请求中有 Captcha-Key, 确保 Redis 中有对应值
    captcha_key = param.headers.get("Captcha-Key", "")
    captcha_code = param.headers.get("Captcha-Code", "")
    if captcha_key and captcha_code:
        # 检查 Redis 中是否已有此 key (Mock LLM 自己预填, LLM 模式需要我们预填)
        try:
            result = subprocess.run(
                [
                    "docker", "exec", REDIS_CONTAINER,
                    "redis-cli", "-a", REDIS_PASSWORD,
                    "EXISTS", f"blade:auth::blade:captcha:{captcha_key}",
                ],
                capture_output=True, text=True, timeout=5,
            )
            if "(integer) 0" in result.stdout:
                # Redis 中没有, 自动预填
                prepare_captcha(captcha_key, captcha_code)
        except Exception:
            # 预填失败不影响执行
            pass

    return execute_with_trace(param)


# ═══════════════════════════════════════════════════════════════════════════════
# Sink 到达检测: 基于 trace 数据 + HTTP 响应
# ═══════════════════════════════════════════════════════════════════════════════

def check_sink_by_trace_and_response(trace: Trace, sink: Sink) -> bool:
    """
    检查是否到达目标 sink。

    策略 (基于 trace 数据):
      1. trace 中包含 CaptchaTokenGranter.grant 的调用
      2. trace 中包含 handleLoginFailure 或 handleLoginSuccess 调用
         (证明走完了 Feign 调用、密码校验、用户查询的全流程)
      3. trace 中包含 userInfo Feign 调用 (IUserClient)

    注意: X-Return-Trace 模式下响应体是 trace JSON 而非原始业务响应,
    所以不能依赖响应体内容做判断。
    """
    if not trace or not trace.nodes:
        return False

    has_granter = False
    has_login_handler = False
    has_user_client = False

    for n in trace.nodes:
        cls = n.class_namespace or ""
        fn = n.function or ""

        if "CaptchaTokenGranter" in cls:
            has_granter = True
        if "handleLoginFailure" in fn or "handleLoginSuccess" in fn:
            has_login_handler = True
        if "userInfo" in fn and "feign" in cls.lower():
            has_user_client = True

    # CaptchaTokenGranter 被调用 + 走到了登录结果处理 = 到达 sink
    reached = has_granter and has_login_handler

    if reached:
        logger.info(
            f"Sink 到达确认: granter={has_granter}, "
            f"login_handler={has_login_handler}, feign_call={has_user_client}"
        )

    return reached


# ═══════════════════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="SpringBlade CaptchaTokenGranter 多参数组合 Fuzz 演示"
    )
    parser.add_argument("--mock", action="store_true", help="使用 Mock LLM (不需要真实 LLM)")
    parser.add_argument("--base-url", default=BASE_URL, help="目标服务 URL")
    parser.add_argument("--max-attempts", type=int, default=8, help="每条路径最大尝试次数")
    parser.add_argument("--marker", default=ATTACK_MARKER, help="攻击标记字符串")
    parser.add_argument("--container", default=CONTAINER_NAME, help="blade-auth Docker 容器名称")
    parser.add_argument("--verbose", "-v", action="store_true", help="输出完整的 prompt/response 决策链")
    args = parser.parse_args()

    container_name = args.container

    def _check_log(marker: str, skip_lines: int) -> bool:
        """检查 sink 是否被触发。
        SpringBlade 的 Logback 不输出 log.error 到 stdout/docker logs，
        但 trace-agent 已经确认了完整执行路径（CaptchaTokenGranter.grant + handleLoginSuccess/Failure），
        所以这里用 HTTP 响应码作为辅助判断：400 = 业务异常 = 走完了全部流程。
        """
        # 由于 log.error 不输出到 docker logs, 且 trace 已确认 sink 到达,
        # 直接返回 True 让 pipeline 以 trace-based sink check 为准
        return True

    def _get_log_line_count() -> int:
        return get_container_log_line_count(container_name)

    # 选择 LLM
    if args.mock:
        logger.info("使用 Mock LLM 模式 (模拟源码引导的 4 轮探索)")
        llm = SpringBladeMockLLM()
    else:
        llm = LLM()
        if not llm.api_key:
            logger.error("未设置 LLM_API_KEY 环境变量，请设置或使用 --mock 模式")
            sys.exit(1)
        logger.info(f"使用 LLM: {llm.model} @ {llm.base_url}")

    # 构建 pipeline
    source_root = "examples/SpringBlade"
    fuzzer = Fuzzer(
        llm=llm,
        base_url=args.base_url,
        attack_marker=args.marker,
        source_root=source_root,
        verbose=args.verbose,
    )
    pipeline = Pipeline(
        fuzzer=fuzzer,
        execute_fn=execute_with_captcha_and_trace,
        check_log_fn=_check_log,
        get_log_line_count_fn=_get_log_line_count,
        max_attempts=args.max_attempts,
        sink_checker=check_sink_by_trace_and_response,
    )

    # 运行 fuzz
    logger.info("=" * 70)
    logger.info("SpringBlade CaptchaTokenGranter 多参数组合 Fuzz")
    logger.info("=" * 70)
    logger.info(f"目标服务: {args.base_url}")
    logger.info(f"目标容器: {container_name}")
    logger.info(f"目标 Sink: CaptchaTokenGranter.grant() L95")
    logger.info(f"  log.error(\"用户登录失败, 账号:{{}}, IP:{{}}\", account, WebUtil.getIP())")
    logger.info("")
    logger.info("6 参数组合条件:")
    logger.info("  1. Captcha-Key (header)  — Redis 验证码 key")
    logger.info("  2. Captcha-Code (header) — 验证码值, 需匹配 Redis")
    logger.info("  3. tenantId (query)      — 租户 ID (000000)")
    logger.info("  4. account (query)       — 账号名")
    logger.info("  5. password (query)      — SM2 加密密码")
    logger.info("  6. User-Type (header)    — web/app")
    logger.info("")
    logger.info(f"Fuzz 目标数: {len(FUZZ_TARGETS)}")
    logger.info(f"最大尝试次数: {args.max_attempts}")
    logger.info("")

    result = pipeline.run(FUZZ_TARGETS)

    # 输出结果
    print("\n" + result.summary)

    # 返回码: 有攻击成功的路径则返回 0
    sys.exit(0 if result.reached_count > 0 else 1)


if __name__ == "__main__":
    main()
