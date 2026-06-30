package org.springblade.auth.tracing;

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
            "execution(* org.springblade.auth..controller..*(..)) || " +
            "execution(* org.springblade.auth..controllers..*(..)) || " +
            "execution(* org.springblade.auth..rest..*(..)) || " +
            "execution(* org.springblade.auth..restapi..*(..)) || " +
            "execution(* org.springblade.auth..endpoint..*(..)) || " +
            "execution(* org.springblade.auth..service..*(..)) || " +
            "execution(* org.springblade.auth..biz..*(..)) || " +
            "execution(* org.springblade.auth..manager..*(..)) || " +
            "execution(* org.springblade.auth.tracesmoke.TraceSmokeService.*(..))" +
            ") && " +
            "!within(org.springblade.auth.tracing..*) && " +
            "!within(org.springblade.auth.tracesmoke.TraceSmokeFilter) && " +
            "!within(org.springblade.auth.tracesmoke.TraceSmokeController)")
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
