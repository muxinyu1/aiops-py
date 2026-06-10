package com.example.microservice.service;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;

/**
 * In-memory user service for demo/testing purposes.
 */
@Service
public class UserService {

    private static final Logger log = LoggerFactory.getLogger(UserService.class);

    private final Map<Long, Map<String, Object>> store = new ConcurrentHashMap<>();
    private final AtomicLong idGen = new AtomicLong(1);

    public UserService() {
        // seed some test data
        create("Alice", "alice@example.com");
        create("Bob", "bob@example.com");
        log.info("UserService initialised with {} seed users", store.size());
    }

    public Map<String, Object> findById(Long id) {
        log.debug("UserService.findById: id={}", id);
        Map<String, Object> user = store.get(id);
        if (user == null) {
            log.warn("UserService.findById: no user with id={}", id);
            throw new IllegalArgumentException("User not found: " + id);
        }
        return user;
    }

    public Map<String, Object> create(String name, String email) {
        if (name == null || name.isBlank()) {
            throw new IllegalArgumentException("User name must not be blank");
        }
        if (email == null || !email.contains("@")) {
            throw new IllegalArgumentException("Invalid email address: " + email);
        }
        long id = idGen.getAndIncrement();
        Map<String, Object> user = Map.of("id", id, "name", name, "email", email);
        store.put(id, user);
        log.debug("UserService.create: stored user id={}", id);
        return user;
    }
}
