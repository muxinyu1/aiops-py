package com.aiops.trace.agent;

import net.bytebuddy.asm.Advice;

import javax.servlet.ServletRequest;
import javax.servlet.ServletResponse;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import java.util.Base64;
import java.util.List;
import java.util.UUID;

/**
 * Byte Buddy Advice for org.apache.catalina.core.ApplicationFilterChain.doFilter().
 * This is the outermost entry point for every HTTP request in embedded Tomcat.
 * We activate TraceContext here so all downstream filters and servlets are covered.
 */
public class FilterChainAdvice {

    private static final String REQUEST_HEADER = "X-Return-Trace";
    private static final String RESPONSE_HEADER = "X-Execution-Trace";

    @Advice.OnMethodEnter(suppress = Throwable.class)
    public static boolean onEnter(@Advice.Argument(0) ServletRequest request) {
        // Only activate once per request (avoid re-entry from nested doFilter calls)
        if (TraceContext.isActive()) {
            return false;
        }

        if (!(request instanceof HttpServletRequest)) {
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
                String json = SpanRecord.toJsonArray(spans);
                String encoded = Base64.getEncoder().encodeToString(json.getBytes("UTF-8"));
                // Always overwrite - our agent trace is more complete than the old one
                httpResp.setHeader(RESPONSE_HEADER, encoded);
            }
        } catch (Exception e) {
            // Silently ignore
        }
    }
}
