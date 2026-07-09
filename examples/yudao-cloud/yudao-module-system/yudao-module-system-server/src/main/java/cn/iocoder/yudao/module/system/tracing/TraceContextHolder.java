package cn.iocoder.yudao.module.system.tracing;

import java.util.ArrayDeque;
import java.util.Deque;

public final class TraceContextHolder {
    private static final ThreadLocal<String> TRACE_ID = new ThreadLocal<>();
    private static final ThreadLocal<Deque<String>> SPAN_STACK =
            ThreadLocal.withInitial(ArrayDeque::new);

    private TraceContextHolder() {}

    public static void set(String traceId)  { TRACE_ID.set(traceId); }
    public static String get()              { return TRACE_ID.get(); }
    public static void clear() {
        TRACE_ID.remove();
        SPAN_STACK.remove();
    }

    /** Get current parent span ID (top of stack), or empty string if root. */
    public static String currentParentSpanId() {
        Deque<String> stack = SPAN_STACK.get();
        return stack.isEmpty() ? "" : stack.peek();
    }

    /** Push span ID onto stack (entering a method). */
    public static void pushSpan(String spanId) {
        SPAN_STACK.get().push(spanId);
    }

    /** Pop span ID from stack (exiting a method). */
    public static void popSpan() {
        Deque<String> stack = SPAN_STACK.get();
        if (!stack.isEmpty()) {
            stack.pop();
        }
    }
}
