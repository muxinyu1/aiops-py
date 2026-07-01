package com.aiops.trace.agent;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.ArrayDeque;
import java.util.Deque;

/**
 * Lightweight helper injected into the bootstrap classloader.
 * Manages a ThreadLocal span stack for parent-child relationship tracking.
 * Accesses the existing TraceContextHolder and TraceStore via reflection.
 *
 * This class must have ZERO dependencies on any non-JDK classes.
 */
public class SpanStackHelper {

    /** ThreadLocal span stack - tracks nested method calls for parent-child relationships. */
    private static final ThreadLocal<Deque<String>> SPAN_STACK =
            ThreadLocal.withInitial(ArrayDeque::new);

    // Cached reflection targets (lazy-initialized on first use per classloader)
    private static volatile Method traceContextHolderGet;
    private static volatile Method traceStoreAdd;
    private static volatile Object traceStoreInstance;
    private static volatile Class<?> spanRecordClass;
    private static volatile boolean reflectionInitialized = false;
    private static volatile boolean reflectionFailed = false;

    /**
     * Get current trace ID from the existing TraceContextHolder.
     * Returns null if not in a traced request.
     */
    public static String getTraceId(ClassLoader cl) {
        ensureInit(cl);
        if (reflectionFailed || traceContextHolderGet == null) return null;
        try {
            return (String) traceContextHolderGet.invoke(null);
        } catch (Exception e) {
            return null;
        }
    }

    /** Push span ID onto the stack (entering a method). */
    public static void pushSpan(String spanId) {
        SPAN_STACK.get().push(spanId);
    }

    /** Pop span ID from the stack (exiting a method). Returns the popped span ID. */
    public static String popSpan() {
        Deque<String> stack = SPAN_STACK.get();
        return stack.isEmpty() ? null : stack.pop();
    }

    /** Get the current parent span ID (top of stack without removing). */
    public static String peekParentSpan() {
        Deque<String> stack = SPAN_STACK.get();
        return stack.isEmpty() ? "" : stack.peek();
    }

    /**
     * Create a SpanRecord and add it to the existing TraceStore.
     */
    public static void addSpan(ClassLoader cl, String traceId, String spanId,
                               String parentSpanId, String content, String function,
                               String methodSignature, String classNamespace,
                               String srcFile, int lineNumber, long startNs,
                               long durationNs, boolean isError, String errorMsg) {
        ensureInit(cl);
        if (reflectionFailed || traceStoreInstance == null || traceStoreAdd == null || spanRecordClass == null) {
            return;
        }
        try {
            Object record = spanRecordClass.getDeclaredConstructor().newInstance();
            setStr(record, "span_id", spanId);
            setStr(record, "parent_span_id", parentSpanId);
            setStr(record, "trace_id", traceId);
            setStr(record, "content", content);
            setStr(record, "function", function);
            setStr(record, "method_signature", methodSignature);
            setStr(record, "class_namespace", classNamespace);
            setStr(record, "src_file", srcFile);
            setInt(record, "line_number", lineNumber);
            setLong(record, "start_ns", startNs);
            setLong(record, "duration_ns", durationNs);
            setBool(record, "is_error", isError);
            setStr(record, "error_message", errorMsg);
            traceStoreAdd.invoke(traceStoreInstance, traceId, record);
        } catch (Exception e) {
            // silently ignore
        }
    }

    private static synchronized void ensureInit(ClassLoader cl) {
        if (reflectionInitialized || reflectionFailed) return;
        if (cl == null) { reflectionFailed = true; return; }
        try {
            // Find TraceContextHolder
            Class<?> holderClass = findClass(cl, "TraceContextHolder");
            traceContextHolderGet = holderClass.getMethod("get");

            // Find SpanRecord
            spanRecordClass = findClass(cl, "SpanRecord");

            // Find TraceStore
            Class<?> storeClass = findClass(cl, "TraceStore");
            // TraceStore is a Spring @Component. Internally it uses a static ConcurrentHashMap.
            // We create a new instance — since the map is static, it shares state with the Spring bean.
            traceStoreInstance = storeClass.getDeclaredConstructor().newInstance();
            traceStoreAdd = storeClass.getMethod("add", String.class, spanRecordClass);

            reflectionInitialized = true;
            System.out.println("[trace-agent] SpanStackHelper: reflection init OK via " + holderClass.getName());
        } catch (Exception e) {
            reflectionFailed = true;
            System.out.println("[trace-agent] SpanStackHelper: reflection init FAILED: " + e.getMessage());
        }
    }

    private static Class<?> findClass(ClassLoader cl, String simpleName) throws ClassNotFoundException {
        String[] basePackages = {
                "com.ctrip.framework.apollo.adminservice.tracing",
                "com.macro.mall.tracing",
                "com.ruoyi.auth.tracing",
                "org.dromara.auth.tracing",
                "org.springblade.auth.tracing",
                "com.youlai.auth.tracing",
                "com.mall4j.cloud.auth.tracing",
                "com.central.tracing",
                "io.github.xxyopen.novel.book.tracing",
                "cn.iocoder.yudao.module.system.tracing",
                "com.piggymetrics.account.tracing",
                "com.moxi.mogublog.admin.tracing",
                "com.github.wxiaoqi.security.tracing",
                "com.jackson0714.passjava.member.tracing",
                "io.niceseason.gulimall.member.tracing",
                "top.tangyh.lamp.tracing",
                "com.example.microservice.tracing",
                "com.pig4cloud.pig.auth.tracing",
        };
        for (String pkg : basePackages) {
            try {
                return cl.loadClass(pkg + "." + simpleName);
            } catch (ClassNotFoundException ignore) {}
        }
        throw new ClassNotFoundException(simpleName + " not found in known packages");
    }

    private static void setStr(Object obj, String fieldName, Object value) {
        try {
            Field f = obj.getClass().getDeclaredField(fieldName);
            f.setAccessible(true);
            f.set(obj, value);
        } catch (Exception ignore) {}
    }

    private static void setInt(Object obj, String fieldName, int value) {
        try {
            Field f = obj.getClass().getDeclaredField(fieldName);
            f.setAccessible(true);
            f.setInt(obj, value);
        } catch (Exception ignore) {}
    }

    private static void setLong(Object obj, String fieldName, long value) {
        try {
            Field f = obj.getClass().getDeclaredField(fieldName);
            f.setAccessible(true);
            f.setLong(obj, value);
        } catch (Exception ignore) {}
    }

    private static void setBool(Object obj, String fieldName, boolean value) {
        try {
            Field f = obj.getClass().getDeclaredField(fieldName);
            f.setAccessible(true);
            f.setBoolean(obj, value);
        } catch (Exception ignore) {}
    }
}
