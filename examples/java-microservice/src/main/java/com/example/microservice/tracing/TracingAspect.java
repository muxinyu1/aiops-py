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
import org.springframework.stereotype.Component;

import java.lang.reflect.Method;
import java.util.Arrays;
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

        try (Scope ignored = span.makeCurrent()) {
            return pjp.proceed();
        } catch (Throwable ex) {
            span.setStatus(StatusCode.ERROR, ex.getMessage());
            span.recordException(ex);
            throw ex;
        } finally {
            span.end();
        }
    }
}
