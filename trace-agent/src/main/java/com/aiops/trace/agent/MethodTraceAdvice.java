package com.aiops.trace.agent;

import net.bytebuddy.asm.Advice;

import java.lang.reflect.Method;
import java.util.UUID;

/**
 * Byte Buddy Advice for method entry/exit. Gets inlined into target methods.
 * Delegates all trace state management to SpanStackHelper (bootstrap classloader).
 */
public class MethodTraceAdvice {

    @Advice.OnMethodEnter(suppress = Throwable.class)
    public static long onEnter(@Advice.Origin Method method) {
        ClassLoader cl = method.getDeclaringClass().getClassLoader();
        String traceId = SpanStackHelper.getTraceId(cl);
        if (traceId == null) {
            return 0L;
        }

        // Generate span ID and push onto stack
        String spanId = UUID.randomUUID().toString().replace("-", "").substring(0, 16);
        SpanStackHelper.pushSpan(spanId);

        return System.nanoTime();
    }

    @Advice.OnMethodExit(onThrowable = Throwable.class, suppress = Throwable.class)
    public static void onExit(@Advice.Enter long startNano,
                              @Advice.Origin Method method,
                              @Advice.Thrown Throwable thrown) {
        if (startNano == 0L) {
            return;
        }

        long durationNs = System.nanoTime() - startNano;

        // Pop our span from stack
        String spanId = SpanStackHelper.popSpan();
        if (spanId == null) {
            return;
        }

        // Parent is now the new top of stack
        String parentSpanId = SpanStackHelper.peekParentSpan();

        ClassLoader cl = method.getDeclaringClass().getClassLoader();
        String traceId = SpanStackHelper.getTraceId(cl);
        if (traceId == null) {
            return;
        }

        // Build span metadata
        Class<?> clazz = method.getDeclaringClass();
        String className = clazz.getSimpleName();
        String methodName = method.getName();
        String namespace = clazz.getName();

        StringBuilder sigBuilder = new StringBuilder(methodName).append("(");
        Class<?>[] paramTypes = method.getParameterTypes();
        for (int i = 0; i < paramTypes.length; i++) {
            if (i > 0) sigBuilder.append(", ");
            sigBuilder.append(paramTypes[i].getSimpleName());
        }
        sigBuilder.append(")");

        long epochNs = System.currentTimeMillis() * 1_000_000L;

        // Get line number
        int lineNo = 0;
        StackTraceElement[] stackTrace = Thread.currentThread().getStackTrace();
        for (StackTraceElement elem : stackTrace) {
            if (elem.getClassName().equals(namespace) && elem.getMethodName().equals(methodName)) {
                lineNo = elem.getLineNumber();
                break;
            }
        }

        boolean isError = thrown != null;
        String errorMsg = isError ? (thrown.getClass().getSimpleName() + ": " + thrown.getMessage()) : null;

        // Add span to existing TraceStore
        SpanStackHelper.addSpan(cl, traceId, spanId, parentSpanId,
                className + "." + methodName, methodName, sigBuilder.toString(),
                namespace, className + ".java", lineNo, epochNs, durationNs, isError, errorMsg);
    }
}
