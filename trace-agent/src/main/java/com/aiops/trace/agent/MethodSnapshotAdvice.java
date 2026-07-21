package com.aiops.trace.agent;

import net.bytebuddy.asm.Advice;
import net.bytebuddy.implementation.bytecode.assign.Assigner;

import java.lang.reflect.Method;
import java.util.UUID;

/**
 * Byte Buddy Advice 增强版 — 在标准 trace 基础上增加运行时变量快照.
 *
 * 所有快照逻辑都委托给 SpanStackHelper,
 * SpanStackHelper 内部通过反射调用应用层的 SnapshotTargetRegistry 和序列化逻辑.
 *
 * 这样设计是因为 Advice 代码会被 inline 到目标方法中,
 * 只能引用 bootstrap classloader 或 agent classloader 中的类.
 */
public class MethodSnapshotAdvice {

    @Advice.OnMethodEnter(suppress = Throwable.class)
    public static long onEnter(@Advice.Origin Method method,
                               @Advice.AllArguments Object[] args,
                               @Advice.This(optional = true) Object self) {
        ClassLoader cl = method.getDeclaringClass().getClassLoader();
        String traceId = SpanStackHelper.getTraceId(cl);
        if (traceId == null) {
            return 0L;
        }

        // Generate span ID and push onto stack
        String spanId = UUID.randomUUID().toString().replace("-", "").substring(0, 16);
        SpanStackHelper.pushSpan(spanId);

        // 委托 SpanStackHelper 处理快照采集 (内部用反射判断 + 序列化)
        SpanStackHelper.captureEntrySnapshot(cl, method, args, self);

        return System.nanoTime();
    }

    @Advice.OnMethodExit(onThrowable = Throwable.class, suppress = Throwable.class)
    public static void onExit(@Advice.Enter long startNano,
                              @Advice.Origin Method method,
                              @Advice.Return(readOnly = true, typing = Assigner.Typing.DYNAMIC) Object returnValue,
                              @Advice.Thrown Throwable thrown,
                              @Advice.This(optional = true) Object self) {
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

        // 委托 SpanStackHelper 处理出口快照并构建完整的 span (含快照)
        SpanStackHelper.captureExitAndAddSpan(cl, traceId, spanId, parentSpanId,
                className + "." + methodName, methodName, sigBuilder.toString(),
                namespace, className + ".java", lineNo, epochNs, durationNs,
                isError, errorMsg, method, returnValue, thrown, self);
    }
}
