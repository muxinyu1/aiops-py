package com.aiops.trace.agent;

import java.util.Collections;
import java.util.HashSet;
import java.util.Set;

/**
 * 运行时快照目标注册表 — 维护当前请求需要变量快照的方法列表.
 *
 * 工作方式:
 *   - TraceFilter 从请求头 X-Snapshot-Methods 中解析目标方法列表
 *   - 格式: "com.example.Service.validate,com.example.Dao.insert" (class_namespace.function)
 *   - 特殊值 "*" 表示所有方法都采集快照
 *   - 存入 ThreadLocal, 供 MethodSnapshotAdvice 在运行时判断
 *
 * 被注入到 bootstrap classloader, 与 SpanStackHelper 一起.
 */
public class SnapshotTargetRegistry {

    /** 当前请求需要快照的方法集合. key = "fully.qualified.ClassName.methodName" */
    private static final ThreadLocal<Set<String>> SNAPSHOT_TARGETS =
            ThreadLocal.withInitial(Collections::emptySet);

    /** 是否对所有方法采集快照 (X-Snapshot-Methods: *) */
    private static final ThreadLocal<Boolean> CAPTURE_ALL =
            ThreadLocal.withInitial(() -> Boolean.FALSE);

    /**
     * 设置当前请求的快照目标方法.
     * 由 TraceFilter 在请求开始时调用.
     *
     * @param methodKeys 逗号分隔的方法标识, 格式 "ClassName.method" 或 "full.package.ClassName.method"
     *                   特殊值 "*" 表示全部方法
     */
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

    /**
     * 检查指定方法是否需要捕获变量快照.
     *
     * @param classNamespace 完全限定类名, e.g. "com.example.service.UserService"
     * @param methodName 方法名, e.g. "validate"
     * @return true 如果需要快照
     */
    public static boolean shouldCapture(String classNamespace, String methodName) {
        if (Boolean.TRUE.equals(CAPTURE_ALL.get())) {
            return true;
        }
        Set<String> targets = SNAPSHOT_TARGETS.get();
        if (targets.isEmpty()) {
            return false;
        }

        // 匹配策略 (多种格式兼容):
        // 1. 完全限定: "com.example.service.UserService.validate"
        String fullKey = classNamespace + "." + methodName;
        if (targets.contains(fullKey)) return true;

        // 2. 简单类名.方法名: "UserService.validate"
        String simpleName = classNamespace.contains(".")
                ? classNamespace.substring(classNamespace.lastIndexOf('.') + 1)
                : classNamespace;
        String simpleKey = simpleName + "." + methodName;
        if (targets.contains(simpleKey)) return true;

        // 3. 仅方法名: "validate" (匹配所有类中该方法)
        if (targets.contains(methodName)) return true;

        // 4. 仅类名: "UserService" 或 "com.example.service.UserService" (匹配该类所有方法)
        if (targets.contains(classNamespace) || targets.contains(simpleName)) return true;

        return false;
    }

    /**
     * 检查当前请求是否有任何快照目标 (用于快速跳过).
     */
    public static boolean hasAnyTarget() {
        return Boolean.TRUE.equals(CAPTURE_ALL.get()) || !SNAPSHOT_TARGETS.get().isEmpty();
    }

    /**
     * 清理当前线程的快照目标. 由 TraceFilter 在请求结束时调用.
     */
    public static void clear() {
        SNAPSHOT_TARGETS.remove();
        CAPTURE_ALL.remove();
    }
}
