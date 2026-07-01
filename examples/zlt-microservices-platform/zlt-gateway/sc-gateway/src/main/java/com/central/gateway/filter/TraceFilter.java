package com.central.gateway.filter;

import com.fasterxml.jackson.databind.ObjectMapper;
import javax.servlet.FilterChain;
import javax.servlet.ServletException;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import org.springframework.web.util.ContentCachingResponseWrapper;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.List;
import java.util.UUID;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;

@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class TraceFilter extends OncePerRequestFilter {

    @Autowired private TraceStore traceStore;
    private final ObjectMapper objectMapper = new ObjectMapper();

    /**
     * JaCoCo agent handle obtained via reflection from org.jacoco.agent.rt.RT.
     * Provides reset() and getExecutionData(boolean) for per-request coverage.
     */
    private static volatile Object jacocoAgent;
    private static volatile java.lang.reflect.Method getExecDataMethod;
    private static volatile java.lang.reflect.Method resetMethod;
    private static volatile boolean jacocoAvailable = true;

    static {
        try {
            Class<?> rtClass = Class.forName("org.jacoco.agent.rt.RT");
            java.lang.reflect.Method getAgent = rtClass.getMethod("getAgent");
            jacocoAgent = getAgent.invoke(null);
            getExecDataMethod = jacocoAgent.getClass().getMethod("getExecutionData", boolean.class);
            resetMethod = jacocoAgent.getClass().getMethod("reset");
        } catch (Exception e) {
            jacocoAvailable = false;
        }
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                    HttpServletResponse response,
                                    FilterChain chain)
            throws ServletException, IOException {

        boolean wantTrace = "true".equalsIgnoreCase(request.getHeader("X-Return-Trace"));
        if (!wantTrace) {
            chain.doFilter(request, response);
            return;
        }

        String traceId = extractTraceId(request.getHeader("traceparent"));
        if (traceId == null) traceId = UUID.randomUUID().toString().replace("-", "");
        TraceContextHolder.set(traceId);

        // Reset JaCoCo coverage before request processing
        resetJacoco();

        ContentCachingResponseWrapper wrappedResponse =
                new ContentCachingResponseWrapper(response);
        try {
            chain.doFilter(request, wrappedResponse);
        } finally {
            try {
                // --- Method-level trace (trace-agent) ---
                List<SpanRecord> spans = traceStore.getAndRemove(traceId);
                String traceJson = "";
                if (!spans.isEmpty()) {
                    traceJson = objectMapper.writeValueAsString(spans);
                    String encoded = Base64.getEncoder()
                            .encodeToString(traceJson.getBytes(StandardCharsets.UTF_8));
                    if (encoded.length() <= 4096) {
                        wrappedResponse.setHeader("X-Execution-Trace", encoded);
                    } else {
                        wrappedResponse.setHeader("X-Execution-Trace", "IN_BODY");
                        wrappedResponse.setHeader("X-Trace-Span-Count", String.valueOf(spans.size()));
                    }
                }

                // --- Line-level coverage (JaCoCo) ---
                byte[] execData = dumpJacoco();
                if (execData != null && execData.length > 0) {
                    String coverageEncoded = Base64.getEncoder().encodeToString(execData);
                    if (coverageEncoded.length() <= 8192) {
                        wrappedResponse.setHeader("X-Coverage-Data", coverageEncoded);
                    } else {
                        wrappedResponse.setHeader("X-Coverage-Data", "IN_BODY");
                    }
                }

                // Handle body fallback when data is too large for headers
                String traceHeader = wrappedResponse.getHeader("X-Execution-Trace");
                String coverageHeader = wrappedResponse.getHeader("X-Coverage-Data");
                boolean traceInBody = "IN_BODY".equals(traceHeader);
                boolean coverageInBody = "IN_BODY".equals(coverageHeader);

                if (traceInBody || coverageInBody) {
                    wrappedResponse.resetBuffer();
                    wrappedResponse.setContentType("application/json");
                    wrappedResponse.setCharacterEncoding("UTF-8");

                    StringBuilder body = new StringBuilder("{");
                    if (traceInBody && !traceJson.isEmpty()) {
                        body.append("\"trace\":").append(traceJson);
                    }
                    if (coverageInBody && execData != null) {
                        if (body.length() > 1) body.append(",");
                        body.append("\"coverageExec\":\"")
                            .append(Base64.getEncoder().encodeToString(execData))
                            .append("\"");
                    }
                    body.append("}");

                    byte[] bodyBytes = body.toString().getBytes(StandardCharsets.UTF_8);
                    wrappedResponse.setContentLength(bodyBytes.length);
                    wrappedResponse.getOutputStream().write(bodyBytes);
                }
            } catch (Exception e) {
                // ignore serialization errors
            } finally {
                TraceContextHolder.clear();
            }
            wrappedResponse.copyBodyToResponse();
        }
    }

    /** Reset JaCoCo counters (discard accumulated data before this request). */
    private void resetJacoco() {
        if (!jacocoAvailable || jacocoAgent == null) return;
        try {
            resetMethod.invoke(jacocoAgent);
        } catch (Exception ignored) {}
    }

    /** Dump JaCoCo exec data for current request and reset counters. */
    private byte[] dumpJacoco() {
        if (!jacocoAvailable || jacocoAgent == null) return null;
        try {
            return (byte[]) getExecDataMethod.invoke(jacocoAgent, true);
        } catch (Exception e) {
            return null;
        }
    }

    private static String extractTraceId(String traceparent) {
        if (traceparent == null) return null;
        String[] parts = traceparent.split("-", -1);
        return (parts.length >= 4 && parts[1].length() == 32) ? parts[1] : null;
    }
}
