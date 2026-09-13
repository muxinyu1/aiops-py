package cn.iocoder.yudao.module.trade.tracing;

import org.aopalliance.intercept.MethodInterceptor;
import org.aopalliance.intercept.MethodInvocation;
import org.springframework.aop.framework.ProxyFactory;
import org.springframework.beans.BeansException;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.config.BeanPostProcessor;
import org.springframework.stereotype.Component;

import java.lang.reflect.Method;
import java.util.Arrays;
import java.util.UUID;
import java.util.stream.Collectors;

/**
 * Programmatically wraps service/biz/manager beans with a tracing proxy.
 * Bypasses @Aspect which doesn't fire in this module due to missing AOP advisors.
 */
@Component
public class TracingBeanPostProcessor implements BeanPostProcessor {

    @Autowired
    private TraceStore traceStore;

    private static boolean shouldProxy(String className) {
        return className.startsWith("cn.iocoder.yudao.module.trade.")
                && (className.contains(".service.") || className.contains(".biz.")
                    || className.contains(".manager.") || className.contains(".convert."))
                && !className.contains(".tracing.");
    }

    @Override
    public Object postProcessAfterInitialization(Object bean, String beanName) throws BeansException {
        String className = bean.getClass().getName();
        // Skip CGLIB proxies already (avoid double-wrapping)
        if (className.contains("$$")) {
            className = bean.getClass().getSuperclass().getName();
        }
        if (!shouldProxy(className)) {
            return bean;
        }

        ProxyFactory factory = new ProxyFactory(bean);
        factory.setProxyTargetClass(true);
        factory.addAdvice(new TracingMethodInterceptor(traceStore));
        return factory.getProxy();
    }

    private static class TracingMethodInterceptor implements MethodInterceptor {
        private final TraceStore traceStore;

        TracingMethodInterceptor(TraceStore traceStore) {
            this.traceStore = traceStore;
        }

        @Override
        public Object invoke(MethodInvocation invocation) throws Throwable {
            String traceId = TraceContextHolder.get();
            if (traceId == null) {
                return invocation.proceed();
            }

            Method method = invocation.getMethod();
            // Skip Object methods
            if (method.getDeclaringClass() == Object.class) {
                return invocation.proceed();
            }

            Class<?> clazz = invocation.getThis().getClass();
            // Unwrap CGLIB class name
            String realClassName = clazz.getName();
            if (realClassName.contains("$$")) {
                clazz = clazz.getSuperclass();
                realClassName = clazz.getName();
            }

            String simpleName = clazz.getSimpleName();
            String methodName = method.getName();
            String paramTypes = Arrays.stream(method.getParameterTypes())
                    .map(Class::getSimpleName).collect(Collectors.joining(", "));

            String spanId = UUID.randomUUID().toString().replace("-", "").substring(0, 16);
            String parentSpanId = TraceContextHolder.currentParentSpanId();
            TraceContextHolder.pushSpan(spanId);

            long epochNs = System.currentTimeMillis() * 1_000_000L;
            long startNs = System.nanoTime();
            boolean isError = false;
            String errorMsg = null;

            try {
                return invocation.proceed();
            } catch (Throwable ex) {
                isError = true;
                errorMsg = ex.getMessage();
                throw ex;
            } finally {
                TraceContextHolder.popSpan();
                long durationNs = System.nanoTime() - startNs;

                SpanRecord record = new SpanRecord();
                record.span_id = spanId;
                record.parent_span_id = parentSpanId;
                record.trace_id = traceId;
                record.content = simpleName + "." + methodName;
                record.function = methodName;
                record.method_signature = methodName + "(" + paramTypes + ")";
                record.class_namespace = realClassName;
                record.src_file = simpleName + ".java";
                record.line_number = -1;
                record.start_ns = epochNs;
                record.duration_ns = durationNs;
                record.is_error = isError;
                record.error_message = errorMsg;
                traceStore.add(traceId, record);
            }
        }
    }
}
