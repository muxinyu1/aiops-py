package com.aiops.trace.agent;

import javax.servlet.*;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.Base64;
import java.util.List;
import java.util.UUID;

/**
 * Servlet Filter that activates tracing when X-Return-Trace header is present.
 * Collects all spans from the execution and returns them in X-Execution-Trace response header.
 *
 * This filter is registered dynamically via ServletContainerInitializer or FilterRegistrationBean.
 */
public class TraceFilter implements Filter {

    private static final String REQUEST_HEADER = "X-Return-Trace";
    private static final String RESPONSE_HEADER = "X-Execution-Trace";

    @Override
    public void init(FilterConfig filterConfig) throws ServletException {
        System.out.println("[trace-agent] TraceFilter initialized");
    }

    @Override
    public void doFilter(ServletRequest request, ServletResponse response, FilterChain chain)
            throws IOException, ServletException {

        if (!(request instanceof HttpServletRequest) || !(response instanceof HttpServletResponse)) {
            chain.doFilter(request, response);
            return;
        }

        HttpServletRequest httpReq = (HttpServletRequest) request;
        HttpServletResponse httpResp = (HttpServletResponse) response;

        String traceHeader = httpReq.getHeader(REQUEST_HEADER);
        if (traceHeader == null || traceHeader.isEmpty()) {
            // No tracing requested, pass through
            chain.doFilter(request, response);
            return;
        }

        // Start trace
        String traceId = UUID.randomUUID().toString().replace("-", "");
        TraceContext.begin(traceId);

        try {
            chain.doFilter(request, response);
        } finally {
            // End trace and collect spans
            List<SpanRecord> spans = TraceContext.end();
            if (!spans.isEmpty()) {
                String json = SpanRecord.toJsonArray(spans);
                String encoded = Base64.getEncoder().encodeToString(json.getBytes("UTF-8"));
                httpResp.setHeader(RESPONSE_HEADER, encoded);
            }
        }
    }

    @Override
    public void destroy() {
    }
}
