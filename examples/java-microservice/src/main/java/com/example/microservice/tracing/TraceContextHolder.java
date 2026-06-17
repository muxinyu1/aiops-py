package com.example.microservice.tracing;

/**
 * Propagates the active trace-id through the current thread so that
 * {@link TracingAspect} can correlate spans with the incoming HTTP request
 * even when the OpenTelemetry SDK is a no-op (e.g. no agent present).
 *
 * <p>Set by {@link TraceFilter} at the start of a traced request and
 * cleared in its finally block.
 */
public final class TraceContextHolder {

    private static final ThreadLocal<String> TRACE_ID = new ThreadLocal<>();

    private TraceContextHolder() {}

    public static void set(String traceId) {
        TRACE_ID.set(traceId);
    }

    public static String get() {
        return TRACE_ID.get();
    }

    public static void clear() {
        TRACE_ID.remove();
    }
}
