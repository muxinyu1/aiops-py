# Java 核心类模板

将所有 `com.example.microservice` 替换为目标项目的根包名。
Spring Boot 2.x 将 `jakarta.servlet` 替换为 `javax.servlet`。

---

## TraceContextHolder.java

```java
package <ROOT_PACKAGE>.tracing;

public final class TraceContextHolder {
    private static final ThreadLocal<String> TRACE_ID = new ThreadLocal<>();
    private TraceContextHolder() {}
    public static void set(String traceId)  { TRACE_ID.set(traceId); }
    public static String get()              { return TRACE_ID.get(); }
    public static void clear()              { TRACE_ID.remove(); }
}
```

---

## SpanRecord.java

```java
package <ROOT_PACKAGE>.tracing;

public class SpanRecord {
    public String span_id;
    public String parent_span_id;
    public String trace_id;
    public String content;           // span 名称, e.g. "AppController.getUser"
    public String function;          // 方法名
    public String method_signature;  // 含参数类型, e.g. "getUser(Long)"
    public String class_namespace;   // 完全限定类名
    public String src_file;          // 源文件名, e.g. "AppController.java"
    public int    line_number;       // 方法首行行号 (从 bytecode 读取), -1 表示未知
    public long   start_ns;          // Unix epoch 纳秒时间戳
    public long   duration_ns;       // 方法耗时 (ns)
    public boolean is_error;
    public String  error_message;    // 仅 is_error=true 时有值
}
```

---

## TraceStore.java

```java
package <ROOT_PACKAGE>.tracing;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.*;
import java.util.concurrent.ConcurrentHashMap;

@Component
public class TraceStore {

    private static final Logger log = LoggerFactory.getLogger(TraceStore.class);
    private static final long TTL_MS = 5 * 60_000L;

    private record Entry(long createdAt, List<SpanRecord> spans) {}

    private final ConcurrentHashMap<String, Entry> store = new ConcurrentHashMap<>();

    public void add(String traceId, SpanRecord record) {
        store.computeIfAbsent(traceId, k ->
            new Entry(System.currentTimeMillis(),
                      Collections.synchronizedList(new ArrayList<>()))
        ).spans().add(record);
    }

    public List<SpanRecord> getAndRemove(String traceId) {
        Entry entry = store.remove(traceId);
        return entry == null ? List.of() : new ArrayList<>(entry.spans());
    }

    @Scheduled(fixedDelay = 60_000)
    public void cleanup() {
        long cutoff = System.currentTimeMillis() - TTL_MS;
        int before = store.size();
        store.entrySet().removeIf(e -> e.getValue().createdAt() < cutoff);
        int removed = before - store.size();
        if (removed > 0) log.debug("TraceStore: evicted {} stale entries", removed);
    }
}
```

> **注意**：`record` 语法需要 Java 16+。Java 11/8 项目将 `record Entry(...)` 替换为普通内部类：
> ```java
> private static class Entry {
>     final long createdAt;
>     final List<SpanRecord> spans;
>     Entry(long createdAt, List<SpanRecord> spans) {
>         this.createdAt = createdAt; this.spans = spans;
>     }
> }
> ```

---

## TraceFilter.java

```java
package <ROOT_PACKAGE>.tracing;

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
 * 当请求含 X-Return-Trace: true 时，
 * 在响应头 X-Execution-Trace 中附加 Base64(JSON) 格式的方法级执行路径。
 */
@Component
public class TraceFilter extends OncePerRequestFilter {

    private static final Logger log = LoggerFactory.getLogger(TraceFilter.class);

    @Autowired private TraceStore traceStore;
    @Autowired private ObjectMapper objectMapper;

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
                    wrappedResponse.setHeader("X-Execution-Trace", encoded);
                    log.debug("TraceFilter: {} spans for trace {}", spans.size(), traceId);
                }
            } catch (Exception e) {
                log.warn("TraceFilter: failed to serialize trace: {}", e.getMessage());
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
```

---

## TracingAspect.java（完整版，无 OTel 依赖时可用此简化版）{#tracingaspect}

下面是**不依赖 OTel SDK** 的最简版本，仅用 TraceContextHolder 传递 trace-id：

```java
package <ROOT_PACKAGE>.tracing;

import org.aspectj.lang.ProceedingJoinPoint;
import org.aspectj.lang.annotation.Around;
import org.aspectj.lang.annotation.Aspect;
import org.aspectj.lang.reflect.MethodSignature;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import java.lang.reflect.Method;
import java.util.Arrays;
import java.util.UUID;
import java.util.stream.Collectors;

@Aspect
@Component
public class TracingAspect {

    @Autowired
    private TraceStore traceStore;

    // ↓ 修改为目标项目实际的 controller/service 包路径
    @Around("execution(* <ROOT_PACKAGE>.controller..*(..)) || " +
            "execution(* <ROOT_PACKAGE>.service..*(..))")
    public Object traceMethod(ProceedingJoinPoint pjp) throws Throwable {

        String traceId = TraceContextHolder.get();
        if (traceId == null) {
            // 未通过 X-Return-Trace 触发，直接透传，零开销
            return pjp.proceed();
        }

        MethodSignature sig      = (MethodSignature) pjp.getSignature();
        Method          method   = sig.getMethod();
        Class<?>        clazz    = pjp.getTarget().getClass();
        String namespace  = clazz.getName();
        String simpleName = clazz.getSimpleName();
        String methodName = method.getName();
        String paramTypes = Arrays.stream(method.getParameterTypes())
                .map(Class::getSimpleName).collect(Collectors.joining(", "));
        String signature  = methodName + "(" + paramTypes + ")";
        int    lineNo     = LineNumberResolver.resolve(clazz, methodName);
        String spanName   = simpleName + "." + methodName;

        String spanId      = UUID.randomUUID().toString().replace("-", "").substring(0, 16);
        long   epochNs     = System.currentTimeMillis() * 1_000_000L;
        long   startNs     = System.nanoTime();
        boolean isError    = false;
        String  errorMsg   = null;

        try {
            return pjp.proceed();
        } catch (Throwable ex) {
            isError  = true;
            errorMsg = ex.getMessage();
            throw ex;
        } finally {
            long durationNs = System.nanoTime() - startNs;
            SpanRecord record       = new SpanRecord();
            record.span_id          = spanId;
            record.parent_span_id   = "";
            record.trace_id         = traceId;
            record.content          = spanName;
            record.function         = methodName;
            record.method_signature = signature;
            record.class_namespace  = namespace;
            record.src_file         = simpleName + ".java";
            record.line_number      = lineNo;
            record.start_ns         = epochNs;
            record.duration_ns      = durationNs;
            record.is_error         = isError;
            record.error_message    = errorMsg;
            traceStore.add(traceId, record);
        }
    }
}
```

> 若项目已有 OTel-based TracingAspect，参考主 SKILL.md 第四步的"4a 已有 TracingAspect"方案，保留原 OTel 逻辑并追加 `traceStore.add()` 即可。

---

## LineNumberResolver.java（按需添加）

若目标项目无此类，也需一并创建（从 `examples/java-microservice` 直接复制即可）：

```
examples/java-microservice/src/main/java/com/example/microservice/tracing/LineNumberResolver.java
```

将包声明改为 `package <ROOT_PACKAGE>.tracing;`，其余内容不变。
