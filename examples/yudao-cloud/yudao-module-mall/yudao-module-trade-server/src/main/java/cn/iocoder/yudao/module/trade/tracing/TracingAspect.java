package cn.iocoder.yudao.module.trade.tracing;

import org.aspectj.lang.ProceedingJoinPoint;
import org.aspectj.lang.annotation.Around;
import org.aspectj.lang.annotation.Aspect;
import org.aspectj.lang.reflect.MethodSignature;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;

import java.lang.reflect.Method;
import java.util.Arrays;
import java.util.UUID;
import java.util.stream.Collectors;

@Aspect
@Component
@Order(Ordered.HIGHEST_PRECEDENCE + 1)
public class TracingAspect {

    @Autowired
    private TraceStore traceStore;

    @Around("(" +
            "execution(* cn.iocoder.yudao.module.trade..controller..*(..)) || " +
            "execution(* cn.iocoder.yudao.module.trade..controllers..*(..)) || " +
            "execution(* cn.iocoder.yudao.module.trade..rest..*(..)) || " +
            "execution(* cn.iocoder.yudao.module.trade..restapi..*(..)) || " +
            "execution(* cn.iocoder.yudao.module.trade..endpoint..*(..)) || " +
            "execution(* cn.iocoder.yudao.module.trade..service..*(..)) || " +
            "execution(* cn.iocoder.yudao.module.trade..biz..*(..)) || " +
            "execution(* cn.iocoder.yudao.module.trade..manager..*(..)) || " +
            "execution(* cn.iocoder.yudao.module.trade.tracesmoke.TraceSmokeService.*(..))" +
            ") && " +
            "!within(cn.iocoder.yudao.module.trade.tracing..*) && " +
            "!within(cn.iocoder.yudao.module.trade.tracesmoke.TraceSmokeFilter) && " +
            "!within(cn.iocoder.yudao.module.trade.tracesmoke.TraceSmokeController)")
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
        int    lineNo     = -1;
        String spanName   = simpleName + "." + methodName;

        String spanId      = UUID.randomUUID().toString().replace("-", "").substring(0, 16);
        // Get parent before pushing self onto stack
        String parentSpanId = TraceContextHolder.currentParentSpanId();
        TraceContextHolder.pushSpan(spanId);

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
        TraceContextHolder.popSpan();
            long durationNs = System.nanoTime() - startNs;
            SpanRecord record       = new SpanRecord();
            record.span_id          = spanId;
            record.parent_span_id   = parentSpanId;
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
