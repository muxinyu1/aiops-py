package com.example.microservice.tracing;

import java.util.Collections;
import java.util.HashSet;
import java.util.Set;

/**
 * 运行时快照目标注册表 — 维护当前请求需要变量快照的方法列表.
 *
 * 工作方式:
 *   - TraceFilter 从请求头 X-Snapshot-Methods 中解析目标方法列表
 *   - 格式: "com.example.Service.validate,com.example.Dao.insert"
 *   - 特殊值 "*" 表示所有方法都采集快照
 *   - 存入 ThreadLocal, 供 trace-agent 在运行时判断
 */
public class SnapshotTargetRegistry {

    private static final ThreadLocal<Set<String>> SNAPSHOT_TARGETS =
            ThreadLocal.withInitial(Collections::emptySet);

    private static final ThreadLocal<Boolean> CAPTURE_ALL =
            ThreadLocal.withInitial(() -> Boolean.FALSE);

    public static void setTargets(String methodKeys) {
        if (methodKeys == null || methodKeys.isEmpty()) {
            SNAPSHOT_TARGETS.set(Collections.emptySet());
            CAPTURE_ALL.set(Boolean.FALSE);
            return;
        }
        if ("*".equals(methodKeys.trim())) {
            CAPTURE_ALL.set(Boolean.TRUE);
            SNAPSHOT_TARGETS.set(Collections.emptySet());
            return;
        }
        Set<String> targets = new HashSet<>();
        for (String key : methodKeys.split(",")) {
            String trimmed = key.trim();
            if (!trimmed.isEmpty()) {
                targets.add(trimmed);
            }
        }
        SNAPSHOT_TARGETS.set(targets);
        CAPTURE_ALL.set(Boolean.FALSE);
    }

    public static boolean shouldCapture(String classNamespace, String methodName) {
        if (Boolean.TRUE.equals(CAPTURE_ALL.get())) {
            return true;
        }
        Set<String> targets = SNAPSHOT_TARGETS.get();
        if (targets.isEmpty()) {
            return false;
        }
        // Full match: "com.example.service.UserService.validate"
        String fullKey = classNamespace + "." + methodName;
        if (targets.contains(fullKey)) return true;
        // Simple class.method: "UserService.validate"
        String simpleName = classNamespace.contains(".")
                ? classNamespace.substring(classNamespace.lastIndexOf('.') + 1)
                : classNamespace;
        if (targets.contains(simpleName + "." + methodName)) return true;
        // Method only: "validate"
        if (targets.contains(methodName)) return true;
        // Class only: "UserService" or full class name
        if (targets.contains(classNamespace) || targets.contains(simpleName)) return true;
        return false;
    }

    public static boolean hasAnyTarget() {
        return Boolean.TRUE.equals(CAPTURE_ALL.get()) || !SNAPSHOT_TARGETS.get().isEmpty();
    }

    public static void clear() {
        SNAPSHOT_TARGETS.remove();
        CAPTURE_ALL.remove();
    }
}
