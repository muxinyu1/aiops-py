"""
demo_fuzz_yudao_trade.py — yudao-cloud trade-server Log Injection Fuzz

目标: 对交易模块的 45 个 logging sinks 进行 fuzz,
验证 LLM 驱动的源码引导 fuzz 能否触达深层业务逻辑中的日志打印点。

运行前提:
  1. trade-server 容器已启动:
     cd examples-yml/yudao-cloud && docker compose -f compose.trade.real.yaml up -d
  2. .env 中配置 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
"""

from __future__ import annotations

import argparse
import logging
import sys

from dotenv import load_dotenv
load_dotenv()

from demo_fuzz import (
    execute_with_trace,
    check_container_log_after_line,
    get_container_log_line_count,
)
from expected_path import APIEntry, ExpectedPath, PathNode, PathSource
from fuzzer import Fuzzer
from llm import LLM
from pipeline import Pipeline
from sink import Sink, SinkType

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

BASE_URL = "http://localhost:8080"
CONTAINER_NAME = "trace-real-yudao-cloud-trade-server"
ATTACK_MARKER = "sink_attacked_trade"
SOURCE_ROOT = "examples/yudao-cloud/yudao-module-mall/yudao-module-trade-server/src/main/java"

# ══════════════════════════════════════════════════════════════════════════════
# API 入口定义
# ══════════════════════════════════════════════════════════════════════════════

API_AFTER_SALE_UPDATE_REFUNDED = APIEntry(
    class_name="cn.iocoder.yudao.module.trade.controller.admin.aftersale.AfterSaleController",
    method="updateAfterSaleRefunded",
    http_method="POST",
    http_path="/admin-api/trade/after-sale/update-refunded",
    src_file="AfterSaleController.java",
    line_number=138,
)

API_ORDER_DELIVERY = APIEntry(
    class_name="cn.iocoder.yudao.module.trade.controller.admin.order.TradeOrderController",
    method="deliveryOrder",
    http_method="PUT",
    http_path="/admin-api/trade/order/delivery",
    src_file="TradeOrderController.java",
    line_number=110,
)

API_ORDER_UPDATE_REMARK = APIEntry(
    class_name="cn.iocoder.yudao.module.trade.controller.admin.order.TradeOrderController",
    method="updateOrderRemark",
    http_method="PUT",
    http_path="/admin-api/trade/order/update-remark",
    src_file="TradeOrderController.java",
    line_number=118,
)

API_ORDER_UPDATE_PRICE = APIEntry(
    class_name="cn.iocoder.yudao.module.trade.controller.admin.order.TradeOrderController",
    method="updateOrderPrice",
    http_method="PUT",
    http_path="/admin-api/trade/order/update-price",
    src_file="TradeOrderController.java",
    line_number=126,
)

API_ORDER_UPDATE_ADDRESS = APIEntry(
    class_name="cn.iocoder.yudao.module.trade.controller.admin.order.TradeOrderController",
    method="updateOrderAddress",
    http_method="PUT",
    http_path="/admin-api/trade/order/update-address",
    src_file="TradeOrderController.java",
    line_number=134,
)

API_AFTER_SALE_AGREE = APIEntry(
    class_name="cn.iocoder.yudao.module.trade.controller.admin.aftersale.AfterSaleController",
    method="agreeAfterSale",
    http_method="PUT",
    http_path="/admin-api/trade/after-sale/agree",
    src_file="AfterSaleController.java",
    line_number=94,
)

API_AFTER_SALE_REFUND = APIEntry(
    class_name="cn.iocoder.yudao.module.trade.controller.admin.aftersale.AfterSaleController",
    method="refundAfterSale",
    http_method="PUT",
    http_path="/admin-api/trade/after-sale/refund",
    src_file="AfterSaleController.java",
    line_number=129,
)

API_BROKERAGE_WITHDRAW_TRANSFERRED = APIEntry(
    class_name="cn.iocoder.yudao.module.trade.controller.admin.brokerage.BrokerageWithdrawController",
    method="updateBrokerageWithdrawTransferred",
    http_method="POST",
    http_path="/admin-api/trade/brokerage-withdraw/update-transferred",
    src_file="BrokerageWithdrawController.java",
    line_number=85,
)

# ══════════════════════════════════════════════════════════════════════════════
# Sink 定义 (从 sinks/yudao-logging-sinks.json 提取的 trade 模块关键 sink)
# ══════════════════════════════════════════════════════════════════════════════

SINK_AFTER_SALE_REFUND_LOG = Sink(
    class_name="cn.iocoder.yudao.module.trade.controller.admin.aftersale.AfterSaleController",
    method="updateAfterSaleRefunded",
    line_number=142,
    src_file="AfterSaleController.java",
    sink_type=SinkType.LOG_INFO,
    log_message="[updateAfterRefund][notifyReqDTO({})]",
    log_api="log.info",
    tainted_params=["notifyReqDTO"],
)

SINK_BROKERAGE_TRANSFERRED_LOG = Sink(
    class_name="cn.iocoder.yudao.module.trade.controller.admin.brokerage.BrokerageWithdrawController",
    method="updateBrokerageWithdrawTransferred",
    line_number=89,
    src_file="BrokerageWithdrawController.java",
    sink_type=SinkType.LOG_INFO,
    log_message="[updateAfterRefund][notifyReqDTO({})]",
    log_api="log.info",
    tainted_params=["notifyReqDTO"],
)

SINK_VALIDATE_PAY_REFUND_NOT_EXIST = Sink(
    class_name="cn.iocoder.yudao.module.trade.service.aftersale.AfterSaleServiceImpl",
    method="validatePayRefund",
    line_number=429,
    src_file="AfterSaleServiceImpl.java",
    sink_type=SinkType.LOG_ERROR,
    log_message="[validatePayRefund][afterSale({}) payRefund({}) 不存在，请进行处理！]",
    log_api="log.error",
    tainted_params=["afterSale", "payRefundId"],
)

SINK_VALIDATE_PAY_REFUND_NO_RESULT = Sink(
    class_name="cn.iocoder.yudao.module.trade.service.aftersale.AfterSaleServiceImpl",
    method="validatePayRefund",
    line_number=435,
    src_file="AfterSaleServiceImpl.java",
    sink_type=SinkType.LOG_ERROR,
    log_message="[validatePayRefund][afterSale({}) payRefund({}) 无退款结果，请进行处理！payRefund 数据是：{}]",
    log_api="log.error",
    tainted_params=["afterSale", "payRefundId"],
)

SINK_ORDER_LOG_ERROR = Sink(
    class_name="cn.iocoder.yudao.module.trade.framework.order.core.aop.TradeOrderLogAspect",
    method="doAfterReturning",
    line_number=93,
    src_file="TradeOrderLogAspect.java",
    sink_type=SinkType.LOG_ERROR,
    log_message="[doAfterReturning][orderLog({}) 订单日志错误]",
    log_api="log.error",
    tainted_params=["orderLog"],
)

SINK_AFTER_SALE_LOG_ERROR = Sink(
    class_name="cn.iocoder.yudao.module.trade.framework.aftersale.core.aop.AfterSaleLogAspect",
    method="doAfterReturning",
    line_number=91,
    src_file="AfterSaleLogAspect.java",
    sink_type=SinkType.LOG_ERROR,
    log_message="[doAfterReturning][afterSaleLog({}) 日志记录错误]",
    log_api="log.error",
    tainted_params=["afterSaleLog"],
)

SINK_BROKERAGE_ADD_ERROR = Sink(
    class_name="cn.iocoder.yudao.module.trade.service.brokerage.BrokerageRecordServiceImpl",
    method="addBrokerage",
    line_number=81,
    src_file="BrokerageRecordServiceImpl.java",
    sink_type=SinkType.LOG_ERROR,
    log_message="[addBrokerage][增加佣金失败：brokerageEnabled 未配置，userId({}) bizType({}) list({})]",
    log_api="log.error",
    tainted_params=["userId", "bizType", "list"],
)

SINK_BROKERAGE_CANCEL_NOT_EXIST = Sink(
    class_name="cn.iocoder.yudao.module.trade.service.brokerage.BrokerageRecordServiceImpl",
    method="cancelBrokerage",
    line_number=112,
    src_file="BrokerageRecordServiceImpl.java",
    sink_type=SinkType.LOG_ERROR,
    log_message="[cancelBrokerage][bizId({}) bizType({}) 更新为已失效失败：记录不存在]",
    log_api="log.error",
    tainted_params=["bizId", "bizType"],
)

# ══════════════════════════════════════════════════════════════════════════════
# 预期路径 (从 API 入口到 Sink 的调用链)
# ══════════════════════════════════════════════════════════════════════════════

PATH_AFTER_SALE_REFUND_LOG = ExpectedPath(
    api_entry=API_AFTER_SALE_UPDATE_REFUNDED,
    log_sink=SINK_AFTER_SALE_REFUND_LOG,
    nodes=[
        PathNode(
            class_name="cn.iocoder.yudao.module.trade.controller.admin.aftersale.AfterSaleController",
            method="updateAfterSaleRefunded", depth=0,
        ),
    ],
    source=PathSource.TAINT,
    confidence=0.95,
)

PATH_BROKERAGE_TRANSFERRED_LOG = ExpectedPath(
    api_entry=API_BROKERAGE_WITHDRAW_TRANSFERRED,
    log_sink=SINK_BROKERAGE_TRANSFERRED_LOG,
    nodes=[
        PathNode(
            class_name="cn.iocoder.yudao.module.trade.controller.admin.brokerage.BrokerageWithdrawController",
            method="updateBrokerageWithdrawTransferred", depth=0,
        ),
    ],
    source=PathSource.TAINT,
    confidence=0.95,
)

PATH_VALIDATE_PAY_REFUND = ExpectedPath(
    api_entry=API_AFTER_SALE_UPDATE_REFUNDED,
    log_sink=SINK_VALIDATE_PAY_REFUND_NOT_EXIST,
    nodes=[
        PathNode(
            class_name="cn.iocoder.yudao.module.trade.controller.admin.aftersale.AfterSaleController",
            method="updateAfterSaleRefunded", depth=0,
        ),
        PathNode(
            class_name="cn.iocoder.yudao.module.trade.service.aftersale.AfterSaleServiceImpl",
            method="updateAfterSaleRefunded", depth=1,
        ),
        PathNode(
            class_name="cn.iocoder.yudao.module.trade.service.aftersale.AfterSaleServiceImpl",
            method="validatePayRefund", depth=2,
        ),
    ],
    source=PathSource.CALL_GRAPH,
    confidence=0.7,
)

PATH_ORDER_DELIVERY = ExpectedPath(
    api_entry=API_ORDER_DELIVERY,
    log_sink=SINK_ORDER_LOG_ERROR,
    nodes=[
        PathNode(
            class_name="cn.iocoder.yudao.module.trade.controller.admin.order.TradeOrderController",
            method="deliveryOrder", depth=0,
        ),
        PathNode(
            class_name="cn.iocoder.yudao.module.trade.service.order.TradeOrderUpdateServiceImpl",
            method="deliveryOrder", depth=1,
        ),
        PathNode(
            class_name="cn.iocoder.yudao.module.trade.framework.order.core.aop.TradeOrderLogAspect",
            method="doAfterReturning", depth=2,
        ),
    ],
    source=PathSource.CALL_GRAPH,
    confidence=0.6,
)

PATH_ORDER_REMARK = ExpectedPath(
    api_entry=API_ORDER_UPDATE_REMARK,
    log_sink=SINK_ORDER_LOG_ERROR,
    nodes=[
        PathNode(
            class_name="cn.iocoder.yudao.module.trade.controller.admin.order.TradeOrderController",
            method="updateOrderRemark", depth=0,
        ),
        PathNode(
            class_name="cn.iocoder.yudao.module.trade.service.order.TradeOrderUpdateServiceImpl",
            method="updateOrderRemark", depth=1,
        ),
        PathNode(
            class_name="cn.iocoder.yudao.module.trade.framework.order.core.aop.TradeOrderLogAspect",
            method="doAfterReturning", depth=2,
        ),
    ],
    source=PathSource.CALL_GRAPH,
    confidence=0.6,
)

PATH_ORDER_PRICE = ExpectedPath(
    api_entry=API_ORDER_UPDATE_PRICE,
    log_sink=SINK_ORDER_LOG_ERROR,
    nodes=[
        PathNode(
            class_name="cn.iocoder.yudao.module.trade.controller.admin.order.TradeOrderController",
            method="updateOrderPrice", depth=0,
        ),
        PathNode(
            class_name="cn.iocoder.yudao.module.trade.service.order.TradeOrderUpdateServiceImpl",
            method="updateOrderPrice", depth=1,
        ),
        PathNode(
            class_name="cn.iocoder.yudao.module.trade.framework.order.core.aop.TradeOrderLogAspect",
            method="doAfterReturning", depth=2,
        ),
    ],
    source=PathSource.CALL_GRAPH,
    confidence=0.6,
)

PATH_ORDER_ADDRESS = ExpectedPath(
    api_entry=API_ORDER_UPDATE_ADDRESS,
    log_sink=SINK_ORDER_LOG_ERROR,
    nodes=[
        PathNode(
            class_name="cn.iocoder.yudao.module.trade.controller.admin.order.TradeOrderController",
            method="updateOrderAddress", depth=0,
        ),
        PathNode(
            class_name="cn.iocoder.yudao.module.trade.service.order.TradeOrderUpdateServiceImpl",
            method="updateOrderAddress", depth=1,
        ),
        PathNode(
            class_name="cn.iocoder.yudao.module.trade.framework.order.core.aop.TradeOrderLogAspect",
            method="doAfterReturning", depth=2,
        ),
    ],
    source=PathSource.CALL_GRAPH,
    confidence=0.6,
)

PATH_AFTER_SALE_AGREE = ExpectedPath(
    api_entry=API_AFTER_SALE_AGREE,
    log_sink=SINK_AFTER_SALE_LOG_ERROR,
    nodes=[
        PathNode(
            class_name="cn.iocoder.yudao.module.trade.controller.admin.aftersale.AfterSaleController",
            method="agreeAfterSale", depth=0,
        ),
        PathNode(
            class_name="cn.iocoder.yudao.module.trade.service.aftersale.AfterSaleServiceImpl",
            method="agreeAfterSale", depth=1,
        ),
        PathNode(
            class_name="cn.iocoder.yudao.module.trade.framework.aftersale.core.aop.AfterSaleLogAspect",
            method="doAfterReturning", depth=2,
        ),
    ],
    source=PathSource.CALL_GRAPH,
    confidence=0.5,
)

# ══════════════════════════════════════════════════════════════════════════════
# Fuzz 目标列表
# ══════════════════════════════════════════════════════════════════════════════

FUZZ_TARGETS = [
    # 高置信度: controller 层直接 log, 用户输入直接进日志
    (API_AFTER_SALE_UPDATE_REFUNDED, SINK_AFTER_SALE_REFUND_LOG, PATH_AFTER_SALE_REFUND_LOG),
    (API_BROKERAGE_WITHDRAW_TRANSFERRED, SINK_BROKERAGE_TRANSFERRED_LOG, PATH_BROKERAGE_TRANSFERRED_LOG),
    # 中等置信度: 需要穿过 service 层到达深层 log.error
    (API_AFTER_SALE_UPDATE_REFUNDED, SINK_VALIDATE_PAY_REFUND_NOT_EXIST, PATH_VALIDATE_PAY_REFUND),
    # 订单操作 -> TradeOrderLogAspect 日志
    (API_ORDER_DELIVERY, SINK_ORDER_LOG_ERROR, PATH_ORDER_DELIVERY),
    (API_ORDER_UPDATE_REMARK, SINK_ORDER_LOG_ERROR, PATH_ORDER_REMARK),
    (API_ORDER_UPDATE_PRICE, SINK_ORDER_LOG_ERROR, PATH_ORDER_PRICE),
    (API_ORDER_UPDATE_ADDRESS, SINK_ORDER_LOG_ERROR, PATH_ORDER_ADDRESS),
    # 售后操作 -> AfterSaleLogAspect 日志
    (API_AFTER_SALE_AGREE, SINK_AFTER_SALE_LOG_ERROR, PATH_AFTER_SALE_AGREE),
]


# ══════════════════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="yudao-cloud Trade Server Log Injection Fuzz")
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--max-attempts", type=int, default=10)
    parser.add_argument("--marker", default=ATTACK_MARKER)
    parser.add_argument("--container", default=CONTAINER_NAME)
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--targets", type=int, default=0, help="只跑前 N 个目标 (0=全部)")
    args = parser.parse_args()

    container_name = args.container

    def _check_log(marker: str, skip_lines: int) -> bool:
        return check_container_log_after_line(marker, skip_lines, container_name)

    def _get_log_line_count() -> int:
        return get_container_log_line_count(container_name)

    llm = LLM()
    if not llm.api_key:
        logger.error("未设置 LLM_API_KEY，请在 .env 中配置")
        sys.exit(1)
    logger.info(f"LLM: {llm.model} @ {llm.base_url}")

    fuzzer = Fuzzer(
        llm=llm,
        base_url=args.base_url,
        attack_marker=args.marker,
        source_root=SOURCE_ROOT,
        verbose=args.verbose,
    )
    pipeline = Pipeline(
        fuzzer=fuzzer,
        execute_fn=execute_with_trace,
        check_log_fn=_check_log,
        get_log_line_count_fn=_get_log_line_count,
        max_attempts=args.max_attempts,
        log_dir="logs/fuzz/yudao-trade",
    )

    targets = FUZZ_TARGETS
    if args.targets > 0:
        targets = targets[:args.targets]

    logger.info(f"目标服务: {args.base_url}")
    logger.info(f"攻击标记: \"{args.marker}\"")
    logger.info(f"目标容器: {container_name}")
    logger.info(f"Fuzz 目标数: {len(targets)}")
    logger.info("")

    result = pipeline.run(targets)
    print("\n" + result.summary)
    sys.exit(0 if result.reached_count > 0 else 1)


if __name__ == "__main__":
    main()
