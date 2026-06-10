package com.example.microservice.controller;

import com.example.microservice.model.ApiResponse;
import com.example.microservice.service.OrderService;
import com.example.microservice.service.UserService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

@RestController
@RequestMapping("/api")
public class AppController {

    private static final Logger log = LoggerFactory.getLogger(AppController.class);

    private final UserService userService;
    private final OrderService orderService;

    public AppController(UserService userService, OrderService orderService) {
        this.userService = userService;
        this.orderService = orderService;
    }

    // GET /api/health
    @GetMapping("/health")
    public ResponseEntity<ApiResponse<String>> health() {
        log.debug("Health check requested");
        return ResponseEntity.ok(ApiResponse.success("UP"));
    }

    // GET /api/users/{id}
    @GetMapping("/users/{id}")
    public ResponseEntity<ApiResponse<Map<String, Object>>> getUser(@PathVariable Long id) {
        log.info("Fetching user with id={}", id);
        try {
            Map<String, Object> user = userService.findById(id);
            log.info("User found: id={}, name={}", id, user.get("name"));
            return ResponseEntity.ok(ApiResponse.success(user));
        } catch (IllegalArgumentException e) {
            log.warn("User not found: id={}", id);
            return ResponseEntity.status(404).body(ApiResponse.error(e.getMessage()));
        }
    }

    // POST /api/users
    @PostMapping("/users")
    public ResponseEntity<ApiResponse<Map<String, Object>>> createUser(
            @RequestBody Map<String, String> body) {
        String name = body.get("name");
        String email = body.get("email");
        log.info("Creating user: name={}, email={}", name, email);
        try {
            Map<String, Object> created = userService.create(name, email);
            log.info("User created successfully: id={}", created.get("id"));
            return ResponseEntity.status(201).body(ApiResponse.success(created));
        } catch (IllegalArgumentException e) {
            log.error("Failed to create user: {}", e.getMessage());
            return ResponseEntity.badRequest().body(ApiResponse.error(e.getMessage()));
        }
    }

    // GET /api/orders/{id}
    @GetMapping("/orders/{id}")
    public ResponseEntity<ApiResponse<Map<String, Object>>> getOrder(@PathVariable Long id) {
        log.info("Fetching order with id={}", id);
        try {
            Map<String, Object> order = orderService.findById(id);
            log.info("Order found: id={}, status={}", id, order.get("status"));
            return ResponseEntity.ok(ApiResponse.success(order));
        } catch (IllegalArgumentException e) {
            log.warn("Order not found: id={}", id);
            return ResponseEntity.status(404).body(ApiResponse.error(e.getMessage()));
        }
    }

    // POST /api/orders
    @PostMapping("/orders")
    public ResponseEntity<ApiResponse<Map<String, Object>>> createOrder(
            @RequestBody Map<String, Object> body) {
        Long userId = Long.valueOf(body.get("userId").toString());
        String product = body.get("product").toString();
        log.info("Creating order: userId={}, product={}", userId, product);
        try {
            Map<String, Object> created = orderService.create(userId, product);
            log.info("Order created: id={}, userId={}", created.get("id"), userId);
            return ResponseEntity.status(201).body(ApiResponse.success(created));
        } catch (Exception e) {
            log.error("Failed to create order for userId={}: {}", userId, e.getMessage(), e);
            return ResponseEntity.badRequest().body(ApiResponse.error(e.getMessage()));
        }
    }

    // DELETE /api/orders/{id}
    @DeleteMapping("/orders/{id}")
    public ResponseEntity<ApiResponse<String>> cancelOrder(@PathVariable Long id) {
        log.info("Cancelling order id={}", id);
        try {
            orderService.cancel(id);
            log.info("Order cancelled: id={}", id);
            return ResponseEntity.ok(ApiResponse.success("Order " + id + " cancelled"));
        } catch (IllegalArgumentException e) {
            log.warn("Cancel failed, order not found: id={}", id);
            return ResponseEntity.status(404).body(ApiResponse.error(e.getMessage()));
        }
    }
}
