package com.jackson0714.passjava.member.tracing;

import org.aspectj.lang.ProceedingJoinPoint;
import org.aspectj.lang.annotation.Around;
import org.aspectj.lang.annotation.Aspect;
import org.aspectj.lang.reflect.MethodSignature;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import java.lang.reflect.Method;
import java.util.Arrays;
import java.util.UUID;
import java.util.stream.Collectors;

@Aspect
@Component
public class TracingAspect {

    @Autowired
    private TraceStore traceStore;

    @Around("(" +
            "execution(* com.jackson0714.passjava.member..controller..*(..)) || " +
            "execution(* com.jackson0714.passjava.member..controllers..*(..)) || " +
            "execution(* com.jackson0714.passjava.member..rest..*(..)) || " +
            "execution(* com.jackson0714.passjava.member..restapi..*(..)) || " +
            "execution(* com.jackson0714.passjava.member..endpoint..*(..)) || " +
            "execution(* com.jackson0714.passjava.member..service..*(..)) || " +
            "execution(* com.jackson0714.passjava.member..biz..*(..)) || " +
            "execution(* com.jackson0714.passjava.member..manager..*(..)) || " +
            "execution(* com.jackson0714.passjava.member.tracesmoke.TraceSmokeService.*(..))" +
            ") && " +
            "!within(com.jackson0714.passjava.member.tracing..*) && " +
            "!within(com.jackson0714.passjava.member.tracesmoke.TraceSmokeFilter) && " +
            "!within(com.jackson0714.passjava.member.tracesmoke.TraceSmokeController)")
    public Object traceMethod(ProceedingJoinPoint pjp) throws Throwable {

        String traceId = TraceContextHolder.get();
        if (traceId == null) {
            return pjp.proceed();
        }

        MethodSignature sig      = (MethodSignature) pjp.getSignature();
        Method          method   = sig.getMethod();
        Class<?>        clazz    = pjp.getTarget().getClass();
        String namespace  = clazz.getName();
        String simpleName = clazz.getSimpleName();
        String methodName = method.getName();
        String paramTypes = Arrays.stream(method.getParameterTypes())
                .map(Class::getSimpleName).collect(Collectors.joining(", "));
        String signature  = methodName + "(" + paramTypes + ")";
        int    lineNo     = LineNumberResolver.resolve(clazz, methodName);
        String spanName   = simpleName + "." + methodName;

        String spanId      = UUID.randomUUID().toString().replace("-", "").substring(0, 16);
        long   epochNs     = System.currentTimeMillis() * 1_000_000L;
        long   startNs     = System.nanoTime();
        boolean isError    = false;
        String  errorMsg   = null;

        try {
            return pjp.proceed();
        } catch (Throwable ex) {
            isError  = true;
            errorMsg = ex.getMessage();
            throw ex;
        } finally {
            long durationNs = System.nanoTime() - startNs;
            SpanRecord record       = new SpanRecord();
            record.span_id          = spanId;
            record.parent_span_id   = "";
            record.trace_id         = traceId;
            record.content          = spanName;
            record.function         = methodName;
            record.method_signature = signature;
            record.class_namespace  = namespace;
            record.src_file         = simpleName + ".java";
            record.line_number      = lineNo;
            record.start_ns         = epochNs;
            record.duration_ns      = durationNs;
            record.is_error         = isError;
            record.error_message    = errorMsg;
            traceStore.add(traceId, record);
        }
    }
}
