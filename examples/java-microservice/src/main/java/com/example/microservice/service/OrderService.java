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
}
