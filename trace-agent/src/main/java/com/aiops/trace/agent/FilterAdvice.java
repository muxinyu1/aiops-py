package com.aiops.trace.agent;

import net.bytebuddy.asm.Advice;

import javax.servlet.FilterChain;
import javax.servlet.ServletRequest;
import javax.servlet.ServletResponse;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import java.util.Base64;
import java.util.List;
import java.util.UUID;

/**
 * Byte Buddy Advice that intercepts javax.servlet.Filter.doFilter() or
 * javax.servlet.http.HttpServlet.service() to activate tracing.
 *
 * This replaces the need for a separately registered TraceFilter.
 */
public class FilterAdvice {

    private static final String REQUEST_HEADER = "X-Return-Trace";
    private static final String RESPONSE_HEADER = "X-Execution-Trace";

    @Advice.OnMethodEnter(suppress = Throwable.class)
    public static boolean onEnter(@Advice.Argument(0) ServletRequest request) {
        if (!(request instanceof HttpServletRequest)) {
            return false;
        }

        // Only activate if no trace context is already active (avoid double-entry)
        if (TraceContext.isActive()) {
            return false;
        }

        HttpServletRequest httpReq = (HttpServletRequest) request;
        String traceHeader = httpReq.getHeader(REQUEST_HEADER);
        if (traceHeader == null || traceHeader.isEmpty()) {
            return false;
        }

        // Start trace
        String traceId = UUID.randomUUID().toString().replace("-", "");
        TraceContext.begin(traceId);
        return true;
    }

    @Advice.OnMethodExit(onThrowable = Throwable.class, suppress = Throwable.class)
    public static void onExit(@Advice.Enter boolean activated,
                              @Advice.Argument(1) ServletResponse response) {
        if (!activated) {
            return;
        }

        try {
            List<SpanRecord> spans = TraceContext.end();
            if (!spans.isEmpty() && response instanceof HttpServletResponse) {
                HttpServletResponse httpResp = (HttpServletResponse) response;
                // Only set if not already set by old TraceFilter
                if (httpResp.getHeader(RESPONSE_HEADER) == null) {
                    String json = SpanRecord.toJsonArray(spans);
                    String encoded = Base64.getEncoder().encodeToString(json.getBytes("UTF-8"));
                    httpResp.setHeader(RESPONSE_HEADER, encoded);
                }
            }
        } catch (Exception e) {
            // Silently ignore
        }
    }
}
