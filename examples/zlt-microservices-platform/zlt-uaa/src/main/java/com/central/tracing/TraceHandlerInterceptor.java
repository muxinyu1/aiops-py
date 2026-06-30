package com.central.tracing;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;
import org.springframework.web.method.HandlerMethod;
import org.springframework.web.servlet.HandlerInterceptor;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

import java.lang.reflect.Method;
import java.util.Arrays;
import java.util.UUID;
import java.util.stream.Collectors;

@Component
public class TraceHandlerInterceptor implements HandlerInterceptor, WebMvcConfigurer {

    private static final String START_NS_ATTR = TraceHandlerInterceptor.class.getName() + ".startNs";
    private static final String START_EPOCH_NS_ATTR = TraceHandlerInterceptor.class.getName() + ".startEpochNs";

    @Autowired
    private TraceStore traceStore;

    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        registry.addInterceptor(this).addPathPatterns("/**");
    }

    @Override
    public boolean preHandle(jakarta.servlet.http.HttpServletRequest request,
                             jakarta.servlet.http.HttpServletResponse response,
                             Object handler) {
        if (TraceContextHolder.get() != null && handler instanceof HandlerMethod) {
            request.setAttribute(START_NS_ATTR, System.nanoTime());
            request.setAttribute(START_EPOCH_NS_ATTR, System.currentTimeMillis() * 1_000_000L);
        }
        return true;
    }

    @Override
    public void afterCompletion(jakarta.servlet.http.HttpServletRequest request,
                                jakarta.servlet.http.HttpServletResponse response,
                                Object handler,
                                Exception ex) {
        String traceId = TraceContextHolder.get();
        Object startObj = request.getAttribute(START_NS_ATTR);
        Object epochObj = request.getAttribute(START_EPOCH_NS_ATTR);
        if (traceId == null || !(handler instanceof HandlerMethod) || !(startObj instanceof Long)) {
            return;
        }

        HandlerMethod handlerMethod = (HandlerMethod) handler;
        Method method = handlerMethod.getMethod();
        Class<?> clazz = handlerMethod.getBeanType();
        String methodName = method.getName();
        String simpleName = clazz.getSimpleName();
        String paramTypes = Arrays.stream(method.getParameterTypes())
                .map(Class::getSimpleName).collect(Collectors.joining(", "));

        SpanRecord record = new SpanRecord();
        record.span_id = UUID.randomUUID().toString().replace("-", "").substring(0, 16);
        record.parent_span_id = "";
        record.trace_id = traceId;
        record.content = simpleName + "." + methodName;
        record.function = methodName;
        record.method_signature = methodName + "(" + paramTypes + ")";
        record.class_namespace = clazz.getName();
        record.src_file = simpleName + ".java";
        record.line_number = LineNumberResolver.resolve(clazz, methodName);
        record.start_ns = epochObj instanceof Long ? ((Long) epochObj) : System.currentTimeMillis() * 1_000_000L;
        record.duration_ns = System.nanoTime() - ((Long) startObj);
        record.is_error = ex != null || response.getStatus() >= 500;
        record.error_message = ex == null ? null : ex.getMessage();
        traceStore.add(traceId, record);
    }
}
