package com.example.microservice.service;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;

/**
 * In-memory order service for demo/testing purposes.
 */
@Service
public class OrderService {

    private static final Logger log = LoggerFactory.getLogger(OrderService.class);

    private final Map<Long, Map<String, Object>> store = new ConcurrentHashMap<>();
    private final AtomicLong idGen = new AtomicLong(1);

    public Map<String, Object> findById(Long id) {
        log.debug("OrderService.findById: id={}", id);
        Map<String, Object> order = store.get(id);
        if (order == null) {
            log.warn("OrderService.findById: no order with id={}", id);
            throw new IllegalArgumentException("Order not found: " + id);
        }
        return order;
    }

    public Map<String, Object> create(Long userId, String product) {
        if (product == null || product.isBlank()) {
            throw new IllegalArgumentException("Product must not be blank");
        }
        long id = idGen.getAndIncrement();
        Map<String, Object> order = new HashMap<>();
        order.put("id", id);
        order.put("userId", userId);
        order.put("product", product);
        order.put("status", "CREATED");
        order.put("createdAt", Instant.now().toString());
        store.put(id, order);
        log.debug("OrderService.create: stored order id={} for userId={}", id, userId);
        return order;
    }

    public void cancel(Long id) {
        Map<String, Object> order = store.get(id);
        if (order == null) {
            log.warn("OrderService.cancel: no order with id={}", id);
            throw new IllegalArgumentException("Order not found: " + id);
        }
        String prevStatus = (String) order.get("status");
        order.put("status", "CANCELLED");
        log.info("OrderService.cancel: order id={} status changed {} -> CANCELLED", id, prevStatus);
    }

    /**
     * 风控评估靶场: sink 埋在三层参数组合嵌套中.
     * 仅当 level=vip 且 amount>10000 且 channel 以 "app" 开头时, 才到达 log.error.
     * 黑盒即使知道所有字段名, 不看源码也难以猜出这个组合.
     */
    public Map<String, Object> evaluateRisk(String orderId, String level, Long amount, String channel) {
        Map<String, Object> result = new HashMap<>();
        result.put("orderId", orderId);
        result.put("risk", "LOW");

        if ("vip".equals(level)) {
            // 第一层: VIP 用户
            if (amount != null && amount > 10000) {
                // 第二层: 大额订单
                if (channel != null && channel.startsWith("app")) {
                    // 第三层: APP 渠道 —— 三层组合才到达 sink
                    log.error("OrderService.evaluateRisk: VIP大额APP端高风险订单, orderId={}", orderId);
                    result.put("risk", "HIGH");
                } else {
                    result.put("risk", "MEDIUM");
                }
            }
        }
        return result;
    }
}
