package cn.iocoder.yudao.module.trade.tracing;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;
import org.springframework.web.method.HandlerMethod;
import org.springframework.web.servlet.HandlerInterceptor;

import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import java.lang.reflect.Method;
import java.util.Arrays;
import java.util.UUID;
import java.util.stream.Collectors;

/**
 * Spring MVC interceptor that records controller method spans.
 * Unlike @Aspect, this is guaranteed to fire because it hooks directly
 * into DispatcherServlet's handler pipeline.
 */
@Component
public class TracingInterceptor implements HandlerInterceptor {

    private static final String ATTR_START = "tracing.startNs";
    private static final String ATTR_SPAN_ID = "tracing.spanId";

    @Autowired
    private TraceStore traceStore;

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) {
        String traceId = TraceContextHolder.get();
        if (traceId == null || !(handler instanceof HandlerMethod)) {
            return true;
        }
        String spanId = UUID.randomUUID().toString().replace("-", "").substring(0, 16);
        request.setAttribute(ATTR_SPAN_ID, spanId);
        request.setAttribute(ATTR_START, System.nanoTime());
        TraceContextHolder.pushSpan(spanId);
        return true;
    }

    @Override
    public void afterCompletion(HttpServletRequest request, HttpServletResponse response,
                                Object handler, Exception ex) {
        String traceId = TraceContextHolder.get();
        if (traceId == null || !(handler instanceof HandlerMethod)) {
            return;
        }

        HandlerMethod hm = (HandlerMethod) handler;
        Method method = hm.getMethod();
        Class<?> clazz = hm.getBeanType();
        String spanId = (String) request.getAttribute(ATTR_SPAN_ID);
        Long startNs = (Long) request.getAttribute(ATTR_START);
        if (spanId == null || startNs == null) return;

        TraceContextHolder.popSpan();
        long durationNs = System.nanoTime() - startNs;

        String simpleName = clazz.getSimpleName();
        String methodName = method.getName();
        String paramTypes = Arrays.stream(method.getParameterTypes())
                .map(Class::getSimpleName).collect(Collectors.joining(", "));

        SpanRecord record = new SpanRecord();
        record.span_id = spanId;
        record.parent_span_id = null;
        record.trace_id = traceId;
        record.content = simpleName + "." + methodName;
        record.function = methodName;
        record.method_signature = methodName + "(" + paramTypes + ")";
        record.class_namespace = clazz.getName();
        record.src_file = simpleName + ".java";
        record.line_number = -1;
        record.start_ns = System.currentTimeMillis() * 1_000_000L;
        record.duration_ns = durationNs;
        record.is_error = (ex != null);
        record.error_message = (ex != null) ? ex.getMessage() : null;
        traceStore.add(traceId, record);
    }
}
