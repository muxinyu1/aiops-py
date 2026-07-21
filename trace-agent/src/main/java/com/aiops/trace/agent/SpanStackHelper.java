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
 *
 * Span stack: Delegates to the application's TraceContextHolder.pushSpan/popSpan/currentParentSpanId
 * if available, so that trace-agent and TracingAspect share the same span stack.
 * Falls back to a local ThreadLocal stack if the app's TraceContextHolder doesn't have span stack methods.
 */
public class SpanStackHelper {

    /** Fallback ThreadLocal span stack - used only if app's TraceContextHolder lacks stack methods. */
    private static final ThreadLocal<Deque<String>> SPAN_STACK =
            ThreadLocal.withInitial(ArrayDeque::new);

    /** ThreadLocal storage for entry snapshots (args + this at method entry). */
    private static final ThreadLocal<Deque<String[]>> ENTRY_SNAPSHOT_STACK =
            ThreadLocal.withInitial(ArrayDeque::new);

    // Cached reflection targets (lazy-initialized on first use per classloader)
    private static volatile Method traceContextHolderGet;
    private static volatile Method traceStoreAdd;
    private static volatile Object traceStoreInstance;
    private static volatile Class<?> spanRecordClass;
    private static volatile boolean reflectionInitialized = false;
    private static volatile boolean reflectionFailed = false;

    // Span stack delegation to TraceContextHolder (if available)
    private static volatile Method holderPushSpan;
    private static volatile Method holderPopSpan;
    private static volatile Method holderCurrentParentSpanId;
    private static volatile boolean spanStackDelegation = false;

    // Snapshot support — reflection targets for app's SnapshotTargetRegistry
    private static volatile Method snapshotShouldCapture;
    private static volatile boolean snapshotRegistryAvailable = false;

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

    /** Push span ID onto the stack (entering a method). Syncs to TraceContextHolder if available. */
    public static void pushSpan(String spanId) {
        SPAN_STACK.get().push(spanId);
        if (spanStackDelegation) {
            try {
                holderPushSpan.invoke(null, spanId);
            } catch (Exception ignore) {}
        }
    }

    /** Pop span ID from the stack (exiting a method). Returns the popped span ID. Syncs to TraceContextHolder if available. */
    public static String popSpan() {
        if (spanStackDelegation) {
            try {
                holderPopSpan.invoke(null);
            } catch (Exception ignore) {}
        }
        Deque<String> stack = SPAN_STACK.get();
        return stack.isEmpty() ? null : stack.pop();
    }

    /**
     * Get the current parent span ID (top of stack without removing).
     * Uses TraceContextHolder's stack if available (more complete, includes TracingAspect spans).
     */
    public static String peekParentSpan() {
        if (spanStackDelegation) {
            try {
                Object result = holderCurrentParentSpanId.invoke(null);
                return result != null ? (String) result : "";
            } catch (Exception ignore) {}
        }
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

    /**
     * Store entry-time snapshots (args + this) for the current method.
     * Called by MethodSnapshotAdvice.onEnter to pass data to onExit.
     */
    public static void storeEntrySnapshot(String argsJson, String thisJson) {
        ENTRY_SNAPSHOT_STACK.get().push(new String[]{argsJson, thisJson});
    }

    /**
     * Retrieve and remove the entry-time snapshots for the current method.
     * Called by MethodSnapshotAdvice.onExit.
     */
    public static String[] retrieveEntrySnapshot() {
        Deque<String[]> stack = ENTRY_SNAPSHOT_STACK.get();
        return stack.isEmpty() ? null : stack.pop();
    }

    /**
     * Capture entry snapshot: check if this method needs snapshot capture via reflection,
     * then serialize args and this if needed.
     * All logic is self-contained here (no external class references needed by Advice).
     */
    public static void captureEntrySnapshot(ClassLoader cl, java.lang.reflect.Method method,
                                            Object[] args, Object self) {
        ensureInit(cl);
        String namespace = method.getDeclaringClass().getName();
        String methodName = method.getName();

        boolean needSnapshot = shouldCaptureSnapshot(namespace, methodName);
        if (needSnapshot) {
            String argsJson = serializeArgs(method, args);
            String thisJson = serializeThis(self);
            storeEntrySnapshot(argsJson, thisJson);
        } else {
            storeEntrySnapshot(null, null);
        }
    }

    /**
     * Capture exit snapshot, build complete span with snapshot data, and add to TraceStore.
     */
    public static void captureExitAndAddSpan(ClassLoader cl, String traceId, String spanId,
                                              String parentSpanId, String content, String function,
                                              String methodSignature, String classNamespace,
                                              String srcFile, int lineNumber, long startNs,
                                              long durationNs, boolean isError, String errorMsg,
                                              java.lang.reflect.Method method, Object returnValue,
                                              Throwable thrown, Object self) {
        // Retrieve entry snapshots
        String[] entrySnapshots = retrieveEntrySnapshot();
        String argsJson = (entrySnapshots != null) ? entrySnapshots[0] : null;
        String thisEntryJson = (entrySnapshots != null) ? entrySnapshots[1] : null;

        // Check if this method needs snapshot
        String namespace = method.getDeclaringClass().getName();
        String methodName = method.getName();
        boolean needSnapshot = shouldCaptureSnapshot(namespace, methodName);

        String returnJson = null;
        String finalThisJson = null;

        if (needSnapshot) {
            // Capture return value
            if (!isError && returnValue != null) {
                returnJson = serializeReturn(returnValue);
            } else if (isError && thrown != null) {
                returnJson = serializeReturn(thrown);
            }

            // Capture exit this state
            String thisExitJson = serializeThis(self);

            // Combine this snapshots
            if (thisEntryJson != null || thisExitJson != null) {
                if (thisEntryJson != null && thisExitJson != null && !thisEntryJson.equals(thisExitJson)) {
                    finalThisJson = "{\"on_enter\":" + thisEntryJson + ",\"on_exit\":" + thisExitJson + "}";
                } else {
                    finalThisJson = thisEntryJson != null ? thisEntryJson : thisExitJson;
                }
            }
        }

        // Add span with snapshot to TraceStore
        addSpanWithSnapshot(cl, traceId, spanId, parentSpanId, content, function,
                methodSignature, classNamespace, srcFile, lineNumber, startNs,
                durationNs, isError, errorMsg, argsJson, returnJson, finalThisJson);
    }

    // ── Snapshot helper: check SnapshotTargetRegistry via reflection ──

    private static boolean shouldCaptureSnapshot(String classNamespace, String methodName) {
        if (!snapshotRegistryAvailable || snapshotShouldCapture == null) return false;
        try {
            Object result = snapshotShouldCapture.invoke(null, classNamespace, methodName);
            return Boolean.TRUE.equals(result);
        } catch (Exception e) {
            return false;
        }
    }

    // ── Snapshot serialization (inline, zero external dependencies) ──

    private static final int MAX_STR_LEN = 200;
    private static final int MAX_ITEMS = 10;
    private static final int MAX_FIELDS = 20;
    private static final int MAX_JSON_LEN = 2000;

    private static String serializeArgs(java.lang.reflect.Method method, Object[] args) {
        if (args == null || args.length == 0) return null;
        StringBuilder sb = new StringBuilder("{");
        java.lang.reflect.Parameter[] params = null;
        try { params = method.getParameters(); } catch (Exception ignore) {}
        for (int i = 0; i < args.length; i++) {
            if (i > 0) sb.append(",");
            String key = (params != null && i < params.length && params[i].isNamePresent())
                    ? params[i].getName() : "arg" + i;
            sb.append("\"").append(key).append("\":");
            sb.append(valueToJson(args[i], true));
            if (sb.length() > MAX_JSON_LEN) { sb.append(",\"_truncated\":true"); break; }
        }
        sb.append("}");
        return sb.toString();
    }

    private static String serializeReturn(Object val) {
        if (val == null) return "{\"value\":null}";
        StringBuilder sb = new StringBuilder("{");
        sb.append("\"type\":\"").append(escJson(val.getClass().getSimpleName())).append("\",");
        sb.append("\"value\":").append(valueToJson(val, true));
        sb.append("}");
        return truncJson(sb.toString());
    }

    private static String serializeThis(Object obj) {
        if (obj == null) return null;
        try {
            StringBuilder sb = new StringBuilder("{");
            sb.append("\"_class\":\"").append(escJson(obj.getClass().getSimpleName())).append("\"");
            Field[] fields = obj.getClass().getDeclaredFields();
            int count = 0;
            for (Field f : fields) {
                if (count >= MAX_FIELDS) break;
                int mod = f.getModifiers();
                if (java.lang.reflect.Modifier.isStatic(mod) || java.lang.reflect.Modifier.isTransient(mod)) continue;
                if (f.isSynthetic() || f.getName().startsWith("this$")) continue;
                try {
                    f.setAccessible(true);
                    Object val = f.get(obj);
                    sb.append(",\"").append(escJson(f.getName())).append("\":");
                    sb.append(valueToJson(val, false));
                    count++;
                } catch (Exception ignore) {}
                if (sb.length() > MAX_JSON_LEN) { sb.append(",\"_truncated\":true"); break; }
            }
            sb.append("}");
            return sb.toString();
        } catch (Exception e) {
            return null;
        }
    }

    private static String valueToJson(Object obj, boolean deep) {
        if (obj == null) return "null";
        if (obj instanceof Number || obj instanceof Boolean) return obj.toString();
        if (obj instanceof String) return "\"" + escJson(truncStr((String) obj)) + "\"";
        if (obj instanceof Character) return "\"" + escJson(obj.toString()) + "\"";
        if (obj.getClass().isEnum()) return "\"" + escJson(((Enum<?>) obj).name()) + "\"";
        if (obj.getClass().isArray()) {
            int len = java.lang.reflect.Array.getLength(obj);
            StringBuilder sb = new StringBuilder("[");
            int lim = Math.min(len, MAX_ITEMS);
            for (int i = 0; i < lim; i++) {
                if (i > 0) sb.append(",");
                sb.append(valueToJson(java.lang.reflect.Array.get(obj, i), false));
            }
            if (len > lim) sb.append(",\"...(").append(len).append(")\"");
            sb.append("]");
            return sb.toString();
        }
        if (!deep) {
            // Shallow: just toString
            try {
                return "\"" + escJson(truncStr(obj.toString())) + "\"";
            } catch (Exception e) {
                return "\"<err>\"";
            }
        }
        // Deep (1-level): expand fields
        if (obj instanceof java.util.Map) {
            StringBuilder sb = new StringBuilder("{");
            int count = 0;
            for (Object entry : ((java.util.Map<?, ?>) obj).entrySet()) {
                if (count >= MAX_ITEMS) { sb.append(",\"_size\":").append(((java.util.Map<?, ?>) obj).size()); break; }
                java.util.Map.Entry<?, ?> e = (java.util.Map.Entry<?, ?>) entry;
                if (count > 0) sb.append(",");
                String k = e.getKey() == null ? "null" : truncStr(e.getKey().toString());
                sb.append("\"").append(escJson(k)).append("\":").append(valueToJson(e.getValue(), false));
                count++;
            }
            sb.append("}");
            return sb.toString();
        }
        if (obj instanceof java.util.Collection) {
            StringBuilder sb = new StringBuilder("[");
            int count = 0;
            for (Object item : (java.util.Collection<?>) obj) {
                if (count >= MAX_ITEMS) { sb.append(",\"...(").append(((java.util.Collection<?>) obj).size()).append(")\""); break; }
                if (count > 0) sb.append(",");
                sb.append(valueToJson(item, false));
                count++;
            }
            sb.append("]");
            return sb.toString();
        }
        // POJO: expand fields
        try {
            StringBuilder sb = new StringBuilder("{\"_type\":\"").append(escJson(obj.getClass().getSimpleName())).append("\"");
            Field[] fields = obj.getClass().getDeclaredFields();
            int count = 0;
            for (Field f : fields) {
                if (count >= MAX_FIELDS) break;
                int mod = f.getModifiers();
                if (java.lang.reflect.Modifier.isStatic(mod) || java.lang.reflect.Modifier.isTransient(mod)) continue;
                if (f.isSynthetic()) continue;
                try {
                    f.setAccessible(true);
                    sb.append(",\"").append(escJson(f.getName())).append("\":").append(valueToJson(f.get(obj), false));
                    count++;
                } catch (Exception ignore) {}
            }
            sb.append("}");
            return sb.toString();
        } catch (Exception e) {
            try { return "\"" + escJson(truncStr(obj.toString())) + "\""; }
            catch (Exception e2) { return "\"<err>\""; }
        }
    }

    private static String truncStr(String s) {
        if (s == null) return "";
        return s.length() <= MAX_STR_LEN ? s : s.substring(0, MAX_STR_LEN) + "...";
    }

    private static String truncJson(String s) {
        return s.length() <= MAX_JSON_LEN ? s : s.substring(0, MAX_JSON_LEN) + "...\"}";
    }

    private static String escJson(String s) {
        if (s == null) return "";
        StringBuilder sb = new StringBuilder(s.length());
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '\\': sb.append("\\\\"); break;
                case '"': sb.append("\\\""); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                default: if (c < 0x20) sb.append(String.format("\\u%04x", (int)c)); else sb.append(c);
            }
        }
        return sb.toString();
    }

    /**
     * Create a SpanRecord with variable snapshots and add it to the existing TraceStore.
     */
    public static void addSpanWithSnapshot(ClassLoader cl, String traceId, String spanId,
                                           String parentSpanId, String content, String function,
                                           String methodSignature, String classNamespace,
                                           String srcFile, int lineNumber, long startNs,
                                           long durationNs, boolean isError, String errorMsg,
                                           String argsSnapshot, String returnSnapshot, String thisSnapshot) {
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
            // Snapshot fields
            setStr(record, "args_snapshot", argsSnapshot);
            setStr(record, "return_snapshot", returnSnapshot);
            setStr(record, "this_snapshot", thisSnapshot);
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

            // Try to find span stack methods on TraceContextHolder (new API)
            try {
                holderPushSpan = holderClass.getMethod("pushSpan", String.class);
                holderPopSpan = holderClass.getMethod("popSpan");
                holderCurrentParentSpanId = holderClass.getMethod("currentParentSpanId");
                spanStackDelegation = true;
                System.out.println("[trace-agent] SpanStackHelper: span stack delegation ENABLED");
            } catch (NoSuchMethodException e) {
                // Old TraceContextHolder without span stack - use local fallback
                spanStackDelegation = false;
                System.out.println("[trace-agent] SpanStackHelper: span stack delegation disabled (old TraceContextHolder)");
            }

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

            // Try to find SnapshotTargetRegistry in app classloader
            try {
                Class<?> registryClass = findClass(cl, "SnapshotTargetRegistry");
                snapshotShouldCapture = registryClass.getMethod("shouldCapture", String.class, String.class);
                snapshotRegistryAvailable = true;
                System.out.println("[trace-agent] SpanStackHelper: SnapshotTargetRegistry FOUND");
            } catch (Exception e) {
                snapshotRegistryAvailable = false;
                System.out.println("[trace-agent] SpanStackHelper: SnapshotTargetRegistry not found (snapshots disabled): " + e.getMessage());
            }
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
