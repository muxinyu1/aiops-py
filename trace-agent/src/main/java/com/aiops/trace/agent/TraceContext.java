package com.aiops.trace.agent;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;

/**
 * Thread-local trace context that maintains:
 * 1. Current trace ID (set by TraceFilter when X-Return-Trace is present)
 * 2. A span stack for building parent-child relationships
 * 3. Collected spans for the current request
 */
public final class TraceContext {

    private static final ThreadLocal<TraceContext> HOLDER = new ThreadLocal<>();

    private String traceId;
    private final Deque<String> spanStack = new ArrayDeque<>();
    private final List<SpanRecord> spans = new ArrayList<>();

    private TraceContext(String traceId) {
        this.traceId = traceId;
    }

    /** Start a new trace for the current thread. Called by TraceFilter. */
    public static void begin(String traceId) {
        HOLDER.set(new TraceContext(traceId));
    }

    /** Get current context, or null if not tracing. */
    public static TraceContext current() {
        return HOLDER.get();
    }

    /** End tracing, return collected spans, clean up. */
    public static List<SpanRecord> end() {
        TraceContext ctx = HOLDER.get();
        HOLDER.remove();
        if (ctx == null) {
            return new ArrayList<>();
        }
        return ctx.spans;
    }

    /** Check if we are currently in a trace. */
    public static boolean isActive() {
        return HOLDER.get() != null;
    }

    public String getTraceId() {
        return traceId;
    }

    /** Get current parent span id (top of stack), or empty string if root. */
    public String currentParentSpanId() {
        String top = spanStack.peek();
        return top != null ? top : "";
    }

    /** Push a new span onto the stack (entering a method). */
    public void pushSpan(String spanId) {
        spanStack.push(spanId);
    }

    /** Pop span from stack (exiting a method). */
    public void popSpan() {
        if (!spanStack.isEmpty()) {
            spanStack.pop();
        }
    }

    /** Add a completed span record. */
    public void addSpan(SpanRecord record) {
        spans.add(record);
    }

    /** Peek at the spans without ending the trace (used by Advice to update duration). */
    public static List<SpanRecord> peekSpans(TraceContext ctx) {
        return ctx.spans;
    }
}
