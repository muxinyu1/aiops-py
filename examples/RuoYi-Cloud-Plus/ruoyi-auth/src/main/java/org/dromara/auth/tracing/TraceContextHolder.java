package org.dromara.auth.tracing;

public final class TraceContextHolder {
    private static final ThreadLocal<String> TRACE_ID = new ThreadLocal<>();
    private TraceContextHolder() {}
    public static void set(String traceId)  { TRACE_ID.set(traceId); }
    public static String get()              { return TRACE_ID.get(); }
    public static void clear()              { TRACE_ID.remove(); }
}
