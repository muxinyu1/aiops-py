package cn.iocoder.yudao.module.system.tracesmoke;

import com.fasterxml.jackson.databind.ObjectMapper;
import cn.iocoder.yudao.module.system.tracing.SpanRecord;
import cn.iocoder.yudao.module.system.tracing.TraceContextHolder;
import cn.iocoder.yudao.module.system.tracing.TraceStore;
import javax.servlet.FilterChain;
import javax.servlet.ServletException;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Profile;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.List;
import java.util.UUID;

@Profile("trace-smoke")
@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class TraceSmokeFilter extends OncePerRequestFilter {

    @Autowired private TraceStore traceStore;
    private final ObjectMapper objectMapper = new ObjectMapper();
    @Autowired private TraceSmokeService traceSmokeService;

    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                    HttpServletResponse response,
                                    FilterChain chain)
            throws ServletException, IOException {
        String path = request.getRequestURI();
        if (!path.endsWith("/__trace_smoke") && !path.endsWith("/api/trace-smoke")) {
            chain.doFilter(request, response);
            return;
        }

        boolean wantTrace = "true".equalsIgnoreCase(request.getHeader("X-Return-Trace"));
        String traceId = UUID.randomUUID().toString().replace("-", "");
        try {
            if (wantTrace) {
                TraceContextHolder.set(traceId);
            }
            String json = objectMapper.writeValueAsString(traceSmokeService.smoke());
            if (wantTrace) {
                List<SpanRecord> spans = traceStore.getAndRemove(traceId);
                if (!spans.isEmpty()) {
                    String spansJson = objectMapper.writeValueAsString(spans);
                    String encoded = Base64.getEncoder()
                            .encodeToString(spansJson.getBytes(StandardCharsets.UTF_8));
                    response.setHeader("X-Execution-Trace", encoded);
                }
            }
            response.setStatus(HttpServletResponse.SC_OK);
            response.setContentType("application/json");
            response.setCharacterEncoding(StandardCharsets.UTF_8.name());
            response.getWriter().write(json);
        } finally {
            TraceContextHolder.clear();
        }
    }
}
