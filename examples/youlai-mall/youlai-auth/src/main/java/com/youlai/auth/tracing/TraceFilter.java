package com.youlai.auth.tracing;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import org.springframework.web.util.ContentCachingResponseWrapper;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.List;
import java.util.UUID;

@Component
public class TraceFilter extends OncePerRequestFilter {

    @Autowired private TraceStore traceStore;
    private final ObjectMapper objectMapper = new ObjectMapper();

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

        ContentCachingResponseWrapper wrappedResponse =
                new ContentCachingResponseWrapper(response);
        try {
            chain.doFilter(request, wrappedResponse);
        } finally {
            try {
                List<SpanRecord> spans = traceStore.getAndRemove(traceId);
                if (!spans.isEmpty()) {
                    String json    = objectMapper.writeValueAsString(spans);
                    String encoded = Base64.getEncoder()
                            .encodeToString(json.getBytes(StandardCharsets.UTF_8));
                    if (encoded.length() <= 4096) {
                        wrappedResponse.setHeader("X-Execution-Trace", encoded);
                    } else {
                        wrappedResponse.setHeader("X-Execution-Trace", "IN_BODY");
                        wrappedResponse.setHeader("X-Trace-Span-Count", String.valueOf(spans.size()));
                        wrappedResponse.resetBuffer();
                        wrappedResponse.setContentType("application/json");
                        wrappedResponse.setCharacterEncoding("UTF-8");
                        byte[] traceBytes = json.getBytes(StandardCharsets.UTF_8);
                        wrappedResponse.setContentLength(traceBytes.length);
                        wrappedResponse.getOutputStream().write(traceBytes);
                    }
                }
            } catch (Exception e) {
                // ignore serialization errors
            } finally {
                TraceContextHolder.clear();
            }
            wrappedResponse.copyBodyToResponse();
        }
    }

    private static String extractTraceId(String traceparent) {
        if (traceparent == null) return null;
        String[] parts = traceparent.split("-", -1);
        return (parts.length >= 4 && parts[1].length() == 32) ? parts[1] : null;
    }
}
