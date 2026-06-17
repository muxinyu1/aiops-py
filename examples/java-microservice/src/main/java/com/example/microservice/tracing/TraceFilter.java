package com.example.microservice.tracing;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import org.springframework.web.util.ContentCachingResponseWrapper;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.List;
import java.util.UUID;

/**
 * Servlet filter that enables <em>inline</em> execution-path tracing.
 *
 * <h3>Protocol</h3>
 * <p>If the incoming HTTP request carries the header
 * {@code X-Return-Trace: true}, the filter:
 * <ol>
 *   <li>Extracts (or generates) a trace-id and stores it in
 *       {@link TraceContextHolder} so that {@link TracingAspect} can
 *       correlate every method span with this request.</li>
 *   <li>Wraps the response with a {@link ContentCachingResponseWrapper} so
 *       that response headers can still be written after the controller
 *       finishes.</li>
 *   <li>After the controller returns, reads all {@link SpanRecord}s from
 *       {@link TraceStore}, serialises them to JSON, Base64-encodes the
 *       result, and attaches it as the response header
 *       {@code X-Execution-Trace}.</li>
 * </ol>
 *
 * <h3>Decoding on the client side</h3>
 * <pre>{@code
 * import base64, json
 * raw = response.headers["X-Execution-Trace"]
 * spans = json.loads(base64.b64decode(raw))
 * }</pre>
 *
 * <p>Requests that do <em>not</em> include {@code X-Return-Trace: true} pass
 * through completely unchanged — no overhead, no memory allocation.
 */
@Component
public class TraceFilter extends OncePerRequestFilter {

    private static final Logger log = LoggerFactory.getLogger(TraceFilter.class);

    @Autowired
    private TraceStore traceStore;

    @Autowired
    private ObjectMapper objectMapper;

    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                    HttpServletResponse response,
                                    FilterChain chain)
            throws ServletException, IOException {

        boolean wantTrace = "true".equalsIgnoreCase(request.getHeader("X-Return-Trace"));

        if (!wantTrace) {
            // Fast path: no tracing requested, zero overhead
            chain.doFilter(request, response);
            return;
        }

        // Determine (or generate) trace-id
        String traceId = extractTraceId(request.getHeader("traceparent"));
        if (traceId == null) {
            traceId = UUID.randomUUID().toString().replace("-", "");
        }
        TraceContextHolder.set(traceId);

        // Buffer the response so we can add headers after the controller runs
        ContentCachingResponseWrapper wrappedResponse =
                new ContentCachingResponseWrapper(response);

        try {
            chain.doFilter(request, wrappedResponse);
        } finally {
            // Collect spans — they are already in the store because TracingAspect
            // runs synchronously and span.end() + store.add() happen before we
            // return from chain.doFilter().
            try {
                List<SpanRecord> spans = traceStore.getAndRemove(traceId);
                if (!spans.isEmpty()) {
                    String json = objectMapper.writeValueAsString(spans);
                    String encoded = Base64.getEncoder()
                            .encodeToString(json.getBytes(StandardCharsets.UTF_8));
                    wrappedResponse.setHeader("X-Execution-Trace", encoded);
                    log.debug("TraceFilter: returned {} spans for trace {}", spans.size(), traceId);
                }
            } catch (Exception e) {
                log.warn("TraceFilter: failed to serialize trace: {}", e.getMessage());
            } finally {
                TraceContextHolder.clear();
            }

            // Flush the buffered response body to the real output stream
            wrappedResponse.copyBodyToResponse();
        }
    }

    /**
     * Extract trace-id from a W3C {@code traceparent} header.
     * Format: {@code 00-<32-hex-trace-id>-<16-hex-parent-id>-<flags>}
     */
    private static String extractTraceId(String traceparent) {
        if (traceparent == null) return null;
        String[] parts = traceparent.split("-", -1);
        if (parts.length >= 4 && parts[1].length() == 32) {
            return parts[1];
        }
        return null;
    }
}
