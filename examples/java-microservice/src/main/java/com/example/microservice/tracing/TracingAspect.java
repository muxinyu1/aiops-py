package com.example.microservice.tracing;

import io.opentelemetry.api.GlobalOpenTelemetry;
import io.opentelemetry.api.trace.Span;
import io.opentelemetry.api.trace.SpanKind;
import io.opentelemetry.api.trace.StatusCode;
import io.opentelemetry.api.trace.Tracer;
import io.opentelemetry.context.Context;
import io.opentelemetry.context.Scope;
import org.aspectj.lang.ProceedingJoinPoint;
import org.aspectj.lang.annotation.Around;
import org.aspectj.lang.annotation.Aspect;
import org.aspectj.lang.reflect.MethodSignature;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import java.lang.reflect.Method;
import java.util.Arrays;
import java.util.UUID;
import java.util.stream.Collectors;

/**
 * AOP aspect that automatically creates an OpenTelemetry child span for every
 * method in the microservice's controller and service layers.
 *
 * Line numbers are resolved by reading the {@code LineNumberTable} attribute
 * from the compiled .class file via {@link LineNumberResolver} (ASM-based).
 * This works with Spring AOP proxy mode — no AspectJ CTW/LTW needed.
 *
 * Each span carries:
 *   code.namespace  — fully-qualified class name
 *   code.function   — method name
 *   code.filepath   — source file name  (e.g. UserService.java)
 *   code.signature  — method signature  (e.g. findById(Long))
 *   code.lineno     — first line of the method body (from bytecode LineNumberTable)
 */
@Aspect
@Component
public class TracingAspect {

    private static final Logger log = LoggerFactory.getLogger(TracingAspect.class);

    /** All-zeros IDs indicate that the OTel SDK is a no-op (no agent). */
    private static final String ZERO_32 = "0".repeat(32);
    private static final String ZERO_16 = "0".repeat(16);

    @Autowired
    private TraceStore traceStore;

    @Around("execution(* com.example.microservice.controller..*(..)) || " +
            "execution(* com.example.microservice.service..*(..))")
    public Object traceMethod(ProceedingJoinPoint pjp) throws Throwable {

        MethodSignature methodSig = (MethodSignature) pjp.getSignature();
        Method          method    = methodSig.getMethod();
        Class<?>        clazz     = pjp.getTarget().getClass();

        String namespace  = clazz.getName();
        String simpleName = clazz.getSimpleName();
        String methodName = method.getName();
        String sourceFile = simpleName + ".java";

        // Build human-readable parameter signature: methodName(Type1, Type2)
        String paramTypes = Arrays.stream(method.getParameterTypes())
                .map(Class::getSimpleName)
                .collect(Collectors.joining(", "));
        String signature = methodName + "(" + paramTypes + ")";

        // Read line number from the class file's LineNumberTable (via ASM)
        int lineNo = LineNumberResolver.resolve(clazz, methodName);

        String spanName = simpleName + "." + methodName;

        Tracer tracer = GlobalOpenTelemetry.getTracer(
                "com.example.microservice.tracing", "0.0.1");

        // Capture parent span ID *before* we create our own span
        String parentSpanId = Span.current().getSpanContext().getSpanId();

        Span span = tracer.spanBuilder(spanName)
                .setSpanKind(SpanKind.INTERNAL)
                .setParent(Context.current())
                .startSpan();

        span.setAttribute("code.namespace", namespace);
        span.setAttribute("code.function",  methodName);
        span.setAttribute("code.filepath",  sourceFile);
        span.setAttribute("code.signature", signature);
        if (lineNo > 0) {
            span.setAttribute("code.lineno", (long) lineNo);
        }

        log.debug("TRACE → {}.{}  ({}:{})", simpleName, methodName, sourceFile, lineNo);

        // Wall-clock start for SpanRecord (epoch + nanoTime offset)
        long startNs    = System.nanoTime();
        long epochNs    = System.currentTimeMillis() * 1_000_000L;

        boolean isError  = false;
        String  errorMsg = null;

        try (Scope ignored = span.makeCurrent()) {
            return pjp.proceed();
        } catch (Throwable ex) {
            isError  = true;
            errorMsg = ex.getMessage();
            span.setStatus(StatusCode.ERROR, ex.getMessage());
            span.recordException(ex);
            throw ex;
        } finally {
            long durationNs = System.nanoTime() - startNs;
            span.end();

            // ── write SpanRecord to TraceStore ────────────────────────────
            String traceId = resolveTraceId(span);
            if (traceId != null) {
                String spanId = span.getSpanContext().getSpanId();
                // Fall back to random IDs when OTel SDK is a no-op
                if (ZERO_16.equals(spanId)) {
                    spanId = UUID.randomUUID().toString().replace("-", "").substring(0, 16);
                }
                if (ZERO_16.equals(parentSpanId)) {
                    parentSpanId = "";
                }

                SpanRecord record       = new SpanRecord();
                record.span_id          = spanId;
                record.parent_span_id   = parentSpanId;
                record.trace_id         = traceId;
                record.content          = spanName;
                record.function         = methodName;
                record.method_signature = signature;
                record.class_namespace  = namespace;
                record.src_file         = sourceFile;
                record.line_number      = lineNo;
                record.start_ns         = epochNs;
                record.duration_ns      = durationNs;
                record.is_error         = isError;
                record.error_message    = errorMsg;

                traceStore.add(traceId, record);
            }
        }
    }

    /**
     * Determine the trace-id to use for {@link TraceStore} indexing.
     *
     * <ol>
     *   <li>Prefer the thread-local set by {@link TraceFilter} — works with
     *       and without the OTel Java agent.</li>
     *   <li>Fall back to the OTel span context trace-id when the agent is
     *       present but no {@code X-Return-Trace} header was sent (e.g. external
     *       monitoring via Jaeger/Zipkin).</li>
     * </ol>
     */
    private String resolveTraceId(Span span) {
        String fromHolder = TraceContextHolder.get();
        if (fromHolder != null) return fromHolder;

        String fromOtel = span.getSpanContext().getTraceId();
        return (fromOtel != null && !ZERO_32.equals(fromOtel)) ? fromOtel : null;
    }
}
