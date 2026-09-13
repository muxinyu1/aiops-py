"""
demo_fuzz_mogublog.py — MoGuBlog editSystemConfig 多参数组合条件 Fuzz 演示

目标: MoGuBlog mogu_admin 的 POST /systemConfig/editSystemConfig
目标 Sink: SystemConfigServiceImpl.editSystemConfig() 中 5 组参数值组合条件

@RequestBody 中 6+ 个字段的枚举值组合决定是否进入 sink:
  Sink1: uploadLocal=0 && uploadQiNiu=0 && uploadMinio=0 → "图片必须选择上传到一个区域"
  Sink2: picturePriority=0(LOCAL) + uploadLocal=0(CLOSE) → "必须开启图片上传本地"
  Sink3: picturePriority=1(QI_NIU) + uploadQiNiu=0(CLOSE) → "必须开启七牛云上传"
  Sink4: picturePriority=2(MINIO) + uploadMinio=0(CLOSE) → "必须开启Minio上传"
  Sink5: startEmailNotification=1(OPEN) + email="" → "必须设置邮箱"

运行前提:
  cd examples-yml/MoGuBlog && docker compose -f compose.real.yaml up -d

使用方式:
  source .env && export LLM_BASE_URL LLM_API_KEY LLM_MODEL
  uv run python demo_fuzz_mogublog.py --max-attempts 8
  uv run python demo_fuzz_mogublog.py --mock
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
# MoGuBlog mogu_admin 手工标注
# ═══════════════════════════════════════════════════════════════════════════════

BASE_URL = "http://localhost:8080"
CONTAINER_NAME = "trace-real-mogublog-mogu-admin"
LOGIN_USERNAME = "admin"
LOGIN_PASSWORD = "mogu2018"
ATTACK_MARKER = "sink_attacked"

# ── API 入口 ──────────────────────────────────────────────────────────────────
API_EDIT_SYSTEM_CONFIG = APIEntry(
    class_name="com.moxi.mogublog.admin.restapi.SystemConfigRestApi",
    method="editSystemConfig",
    http_method="POST",
    http_path="/systemConfig/editSystemConfig",
    src_file="SystemConfigRestApi.java",
    line_number=47,
)

# ── Sink 点 ──────────────────────────────────────────────────────────────────
SINK_PICTURE_MUST_SELECT = Sink(
    class_name="com.moxi.mogublog.xo.service.impl.SystemConfigServiceImpl",
    method="editSystemConfig",
    line_number=99,
    src_file="SystemConfigServiceImpl.java",
    sink_type=SinkType.LOG_ERROR,
    log_message="图片必须选择上传到一个区域 (条件: uploadLocal=0 && uploadQiNiu=0 && uploadMinio=0)",
    log_api="ResultUtil.errorWithMessage",
    tainted_params=["uploadLocal", "uploadQiNiu", "uploadMinio"],
)

SINK_MUST_OPEN_LOCAL = Sink(
    class_name="com.moxi.mogublog.xo.service.impl.SystemConfigServiceImpl",
    method="editSystemConfig",
    line_number=103,
    src_file="SystemConfigServiceImpl.java",
    sink_type=SinkType.LOG_ERROR,
    log_message="必须开启图片上传本地 (条件: picturePriority=0 && uploadLocal=0, 且至少一个upload开启以绕过Sink1)",
    log_api="ResultUtil.errorWithMessage",
    tainted_params=["picturePriority", "contentPicturePriority", "uploadLocal"],
)

SINK_MUST_OPEN_QINIU = Sink(
    class_name="com.moxi.mogublog.xo.service.impl.SystemConfigServiceImpl",
    method="editSystemConfig",
    line_number=109,
    src_file="SystemConfigServiceImpl.java",
    sink_type=SinkType.LOG_ERROR,
    log_message="必须开启七牛云上传 (条件: picturePriority=1 && uploadQiNiu=0, 且绕过Sink1和Sink2)",
    log_api="ResultUtil.errorWithMessage",
    tainted_params=["picturePriority", "contentPicturePriority", "uploadQiNiu"],
)

SINK_MUST_OPEN_MINIO = Sink(
    class_name="com.moxi.mogublog.xo.service.impl.SystemConfigServiceImpl",
    method="editSystemConfig",
    line_number=115,
    src_file="SystemConfigServiceImpl.java",
    sink_type=SinkType.LOG_ERROR,
    log_message="必须开启Minio上传 (条件: picturePriority=2 && uploadMinio=0, 且绕过Sink1-3)",
    log_api="ResultUtil.errorWithMessage",
    tainted_params=["picturePriority", "contentPicturePriority", "uploadMinio"],
)

SINK_MUST_SET_EMAIL = Sink(
    class_name="com.moxi.mogublog.xo.service.impl.SystemConfigServiceImpl",
    method="editSystemConfig",
    line_number=121,
    src_file="SystemConfigServiceImpl.java",
    sink_type=SinkType.LOG_ERROR,
    log_message="必须设置邮箱 (条件: startEmailNotification=1 && email为空, 且绕过Sink1-4)",
    log_api="ResultUtil.errorWithMessage",
    tainted_params=["startEmailNotification", "email"],
)

# ── 预期路径 ──────────────────────────────────────────────────────────────────
PATH_EDIT_SYSTEM_CONFIG = ExpectedPath(
    api_entry=API_EDIT_SYSTEM_CONFIG,
    log_sink=None,  # type: ignore
    nodes=[
        PathNode(class_name="com.moxi.mogublog.admin.restapi.SystemConfigRestApi",
                 method="editSystemConfig", depth=0),
        PathNode(class_name="com.moxi.mogublog.xo.service.impl.SystemConfigServiceImpl",
                 method="editSystemConfig", depth=1),
    ],
    source=PathSource.TAINT,
    confidence=0.9,
)

# ── Fuzz 目标清单 ─────────────────────────────────────────────────────────────
FUZZ_TARGETS = [
    (API_EDIT_SYSTEM_CONFIG, SINK_PICTURE_MUST_SELECT, PATH_EDIT_SYSTEM_CONFIG),
    (API_EDIT_SYSTEM_CONFIG, SINK_MUST_OPEN_LOCAL, PATH_EDIT_SYSTEM_CONFIG),
    (API_EDIT_SYSTEM_CONFIG, SINK_MUST_OPEN_QINIU, PATH_EDIT_SYSTEM_CONFIG),
    (API_EDIT_SYSTEM_CONFIG, SINK_MUST_OPEN_MINIO, PATH_EDIT_SYSTEM_CONFIG),
    (API_EDIT_SYSTEM_CONFIG, SINK_MUST_SET_EMAIL, PATH_EDIT_SYSTEM_CONFIG),
]


# ═══════════════════════════════════════════════════════════════════════════════
# 认证 + 执行器包装
# ═══════════════════════════════════════════════════════════════════════════════

def login(base_url: str) -> str:
    """登录 MoGuBlog admin 并返回 Authorization header 值。"""
    url = f"{base_url}/auth/login?username={LOGIN_USERNAME}&password={LOGIN_PASSWORD}"
    req = urllib.request.Request(url, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())
    if data.get("code") != "success":
        raise RuntimeError(f"Login failed: {data}")
    return data["data"]["token"]


_auth_token: str = ""


def execute_with_auth(param: HttpParameter) -> Trace:
    """包装 execute_with_trace，自动注入 JWT 认证并处理 token 过期。"""
    global _auth_token
    param.headers["Authorization"] = _auth_token
    trace = execute_with_trace(param)

    resp = getattr(trace, "response_body", "")
    if "token无效或过期" in resp:
        logger.info("  Token 过期，重新登录...")
        _auth_token = login(BASE_URL)
        param.headers["Authorization"] = _auth_token
        trace = execute_with_trace(param)

    return trace


def check_sink_by_response(trace: Trace, sink: Sink) -> bool:
    """
    通过 HTTP 响应 body 检查 sink 是否到达。

    MoGuBlog 的 sink 是 ResultUtil.errorWithMessage() 返回的 JSON 错误响应,
    不是日志输出, 所以通过响应内容判断而非 trace 方法匹配。
    """
    resp = getattr(trace, "response_body", "")
    try:
        data = json.loads(resp)
    except Exception:
        return False
    if data.get("code") != "error":
        return False
    msg = data.get("message", "")
    # 每个 sink 用一个唯一关键词匹配实际错误消息
    sink_keyword = {
        99: "上传到一个区域",
        103: "上传本地",
        109: "七牛",
        115: "Minio",
        121: "邮箱",
    }
    keyword = sink_keyword.get(sink.line_number)
    return keyword is not None and keyword in msg


# ═══════════════════════════════════════════════════════════════════════════════
# Mock LLM
# ═══════════════════════════════════════════════════════════════════════════════

class MoGuBlogMockLLM(LLM):
    """
    Mock LLM 模拟两阶段 fuzz:
      Round 1: 不知道值域, 盲猜 "true"/"false" → miss
      Round 2: 收到偏差反馈(含源码片段), 推理出正确值域 → HIT
    """

    def __init__(self):
        super().__init__(base_url="mock://", api_key="mock", model="mock")
        self._call_count = 0

    def chat(self, messages: list[Message]) -> str:
        self._call_count += 1
        # 只看 system 消息来识别目标 sink (避免源码片段中其他 sink 描述干扰)
        system_text = " ".join(m.content for m in messages if m.role == "system")
        has_feedback = any("偏差" in m.content or "未到达" in m.content or "未能到达" in m.content
                          for m in messages if m.role == "user")

        if not has_feedback:
            return self._blackbox_guess()

        # 有偏差反馈后, 根据 system prompt 中的 sink 描述生成正确参数
        # 注意: 检查顺序从具体到通用, 避免通用模式先命中
        if "必须开启图片上传本地" in system_text:
            return self._wrap({"uploadLocal": "0", "uploadQiNiu": "1", "uploadMinio": "0",
                               "picturePriority": "0", "contentPicturePriority": "1"})
        elif "必须开启七牛云上传" in system_text:
            return self._wrap({"uploadLocal": "1", "uploadQiNiu": "0", "uploadMinio": "0",
                               "picturePriority": "1", "contentPicturePriority": "0"})
        elif "必须开启Minio上传" in system_text:
            return self._wrap({"uploadLocal": "1", "uploadQiNiu": "0", "uploadMinio": "0",
                               "picturePriority": "2", "contentPicturePriority": "0"})
        elif "必须设置邮箱" in system_text:
            return self._wrap({"uploadLocal": "1", "uploadQiNiu": "0", "uploadMinio": "0",
                               "picturePriority": "0", "contentPicturePriority": "0",
                               "startEmailNotification": "1", "email": ""})
        elif "必须选择上传到一个区域" in system_text:
            return self._wrap({"uploadLocal": "0", "uploadQiNiu": "0", "uploadMinio": "0",
                               "picturePriority": "0", "contentPicturePriority": "0"})
        return self._blackbox_guess()

    def _blackbox_guess(self) -> str:
        return self._wrap({"uploadLocal": "true", "uploadQiNiu": "false",
                           "uploadMinio": "false", "picturePriority": "local",
                           "contentPicturePriority": "local",
                           "startEmailNotification": "off", "email": "test@example.com"})

    def _wrap(self, body: dict) -> str:
        return "<think>分析参数组合</think>\n<json>\n" + json.dumps({
            "method": "POST",
            "url": f"{BASE_URL}/systemConfig/editSystemConfig",
            "headers": {"Content-Type": "application/json"},
            "body": body,
        }) + "\n</json>"


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    global BASE_URL, _auth_token

    parser = argparse.ArgumentParser(description="MoGuBlog editSystemConfig 参数组合条件 Fuzz Demo")
    parser.add_argument("--mock", action="store_true", help="使用 Mock LLM (不需要真实 LLM)")
    parser.add_argument("--base-url", default=BASE_URL, help="目标服务 URL")
    parser.add_argument("--max-attempts", type=int, default=8, help="每条路径最大尝试次数")
    parser.add_argument("--marker", default=ATTACK_MARKER, help="攻击标记字符串")
    parser.add_argument("--container", default=CONTAINER_NAME, help="目标 Docker 容器名称")
    parser.add_argument("--verbose", "-v", action="store_true", help="输出完整的 prompt/response 决策链")
    args = parser.parse_args()

    BASE_URL = args.base_url
    container_name = args.container

    # MoGuBlog 的 sink 是 HTTP 响应错误 (ResultUtil.errorWithMessage)，
    # 不是日志注入，所以日志检查始终返回 True，让 Pipeline 仅依赖 sink_checker
    def _check_log(marker: str, skip_lines: int) -> bool:
        return True

    def _get_log_line_count() -> int:
        return 0

    # LLM
    if args.mock:
        llm = MoGuBlogMockLLM()
        logger.info("使用 MockLLM (测试模式)")
    else:
        llm = LLM()
        if not llm.api_key:
            logger.error("未设置 LLM_API_KEY，请设置或使用 --mock")
            sys.exit(1)
        logger.info(f"使用 LLM: {llm.model} @ {llm.base_url}")

    # 登录
    logger.info(f"目标服务: {args.base_url}")
    logger.info("登录 MoGuBlog admin ...")
    try:
        _auth_token = login(args.base_url)
        logger.info("登录成功")
    except Exception as e:
        logger.error(f"登录失败: {e}")
        sys.exit(1)

    logger.info(f"Fuzz 目标数: {len(FUZZ_TARGETS)}")
    logger.info("")

    # 构建 Pipeline (复用现有 Fuzzer + Pipeline 的偏差反馈逻辑)
    fuzzer = Fuzzer(
        llm=llm,
        base_url=args.base_url,
        attack_marker=args.marker,
        source_root="examples/MoGuBlog",
        verbose=args.verbose,
    )
    pipeline = Pipeline(
        fuzzer=fuzzer,
        execute_fn=execute_with_auth,
        check_log_fn=_check_log,
        get_log_line_count_fn=_get_log_line_count,
        max_attempts=args.max_attempts,
        sink_checker=check_sink_by_response,
    )

    # 运行
    result = pipeline.run(FUZZ_TARGETS)
    logger.info(f"\n{result.summary}")
    print(f"\n{result.summary}")
    sys.exit(0 if result.reached_count > 0 else 1)


if __name__ == "__main__":
    main()
