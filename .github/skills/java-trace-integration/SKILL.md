---
name: java-trace-integration
description: >
  为 Java Spring Boot 微服务添加"执行路径追踪"能力并构建推送 Docker 镜像。
  适用场景：为 examples/ 下的任意 Java 微服务子模块集成内联追踪（无需外部 OTel Collector），
  使服务在处理正常业务请求的同时，通过 X-Return-Trace 请求头触发、X-Execution-Trace 响应头返回
  方法级执行路径（类名、方法名、源文件行号、耗时、是否出错）。
  关键词：execution trace, tracing, span, java-microservice, docker build, aliyun acr, 追踪集成。
argument-hint: '<project-dir>  例如: examples/PiggyMetrics'
---

# Java 微服务追踪集成工作流

## 目标

在 **不改变任何已有业务 API 行为** 的前提下，为目标 Spring Boot 微服务添加以下能力：

- 请求携带 `X-Return-Trace: true` 头时，响应附带 `X-Execution-Trace` 头
- `X-Execution-Trace` 值为 Base64(JSON)，JSON 是方法级 span 列表，每个 span 含：
  `span_id / parent_span_id / content / function / method_signature / class_namespace / src_file / line_number / start_ns / duration_ns / is_error / error_message`
- 不携带该头的请求零额外开销

---

## 第一步：分析目标项目结构

在开始前，先收集目标项目的以下信息：

1. **根包名**（`src/main/java/` 下第一层包路径，如 `com.example.microservice`）
2. **主应用类**（带 `@SpringBootApplication` 的类，通常在根包下）
3. **已有 AOP/Tracing 代码**（搜索 `@Aspect`、`TracingAspect`）
4. **构建工具**（Maven `pom.xml` 或 Gradle）
5. **Java 版本**（`pom.xml` 中 `<java.version>` 或 Gradle 的 `sourceCompatibility`）

```bash
# 快速定位关键文件
find <project-dir>/src/main/java -name "*.java" | head -30
grep -r "@SpringBootApplication" <project-dir>/src --include="*.java" -l
grep -r "@Aspect" <project-dir>/src --include="*.java" -l
```

---

## 第二步：确认/添加 Maven 依赖

检查 `pom.xml` 是否已有以下依赖，若没有则添加：

```xml
<!-- AOP -->
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-aop</artifactId>
</dependency>

<!-- OpenTelemetry API (仅用于 span 上下文，可选) -->
<dependency>
    <groupId>io.opentelemetry</groupId>
    <artifactId>opentelemetry-api</artifactId>
    <version>1.38.0</version>
</dependency>

<!-- ASM 字节码读取行号 -->
<dependency>
    <groupId>org.ow2.asm</groupId>
    <artifactId>asm</artifactId>
    <version>9.7</version>
</dependency>
```

> Gradle 等价：`implementation 'org.springframework.boot:spring-boot-starter-aop'` 等。

---

## 第三步：创建 4 个追踪核心类

在目标项目的 `tracing` 子包下创建下列文件（将 `<ROOT_PACKAGE>` 替换为实际根包，如 `com.example.microservice`）：

| 文件 | 说明 |
|------|------|
| `TraceContextHolder.java` | ThreadLocal 传递 trace-id |
| `SpanRecord.java` | 单个方法 span 的数据结构 |
| `TraceStore.java` | 按 trace-id 聚合 spans，5 分钟 TTL |
| `TraceFilter.java` | Servlet Filter，负责检测请求头、收集 spans、写响应头 |

完整模板见 [→ Java 核心类模板](./references/java-templates.md)。

### 关键替换点

所有模板中的 `com.example.microservice` 替换为目标项目根包名。  
`TraceFilter` 中的 `import jakarta.servlet.*` 在 Spring Boot 2.x 项目需改为 `import javax.servlet.*`。

---

## 第四步：修改/新建 TracingAspect

### 4a. 项目已有 TracingAspect

在已有 `@Aspect` 类中：

1. 注入 `TraceStore`：
   ```java
   @Autowired
   private TraceStore traceStore;
   ```
2. 在 `@Around` 方法的 `finally` 块末尾（`span.end()` 之后）添加 SpanRecord 写入逻辑：
   ```java
   // finally 块末尾
   String traceId = TraceContextHolder.get();
   if (traceId == null) {
       traceId = span.getSpanContext().getTraceId(); // OTel fallback
   }
   if (traceId != null && !traceId.equals("0".repeat(32))) {
       SpanRecord record = new SpanRecord();
       record.span_id        = span.getSpanContext().getSpanId();
       record.parent_span_id = parentSpanId; // 进入方法前记录的父 span ID
       record.trace_id       = traceId;
       record.content        = spanName;
       record.function       = methodName;
       record.method_signature = signature;
       record.class_namespace  = namespace;
       record.src_file         = sourceFile;
       record.line_number      = lineNo;
       record.start_ns         = epochNs;
       record.duration_ns      = durationNs;
       record.is_error         = isError;
       record.error_message    = errorMsg;
       traceStore.add(traceId, record);
   }
   ```

### 4b. 项目无 TracingAspect

完整参考实现见 [→ TracingAspect 完整模板](./references/java-templates.md#tracingaspect)。  
切入点表达式改为覆盖目标项目的 controller/service 包：
```java
@Around("execution(* <ROOT_PACKAGE>.controller..*(..)) || " +
        "execution(* <ROOT_PACKAGE>.service..*(..))")
```

---

## 第五步：启用调度（@EnableScheduling）

在主应用类上添加 `@EnableScheduling`，以支持 `TraceStore` 的定时清理：

```java
@SpringBootApplication
@EnableScheduling   // ← 添加这一行
public class XxxApplication { ... }

// import org.springframework.scheduling.annotation.EnableScheduling;
```

---

## 第六步：创建 Dockerfile

在项目根目录创建 `Dockerfile`，参考 [→ Dockerfile 模板](./references/dockerfile-template.md)。

关键点：
- **多阶段构建**：`builder` 阶段用 JDK + Maven 编译；`runtime` 阶段用 JRE
- **内嵌 OTel Java agent**：`runtime` 阶段下载 `opentelemetry-javaagent.jar`
- **JAR 名称**：从 `pom.xml` 的 `<artifactId>-<version>` 确认，并更新 `COPY --from=builder` 行
- **Java 版本**：`builder` 和 `runtime` 镜像标签需与项目 Java 版本一致（如 `17-jdk-jammy` / `17-jre-jammy`）

---

## 第七步：构建 Docker 镜像

```bash
REGISTRY="crpi-8tnv6lve87c20oxm.cn-beijing.personal.cr.aliyuncs.com"
# 项目名全小写，用短横线连接
PROJECT_NAME="<lowercase-project-name>"
IMAGE="${REGISTRY}/llmfuzz/${PROJECT_NAME}:latest"

cd <project-dir>
docker build -t "$IMAGE" .
```

常见构建问题及修复见 [→ 构建故障排除](./references/troubleshooting.md)。

---

## 第八步：推送到阿里云 ACR

```bash
docker push "${REGISTRY}/llmfuzz/${PROJECT_NAME}:latest"
```

> 前提：已执行过  
> `docker login --username=aliyun2204462200 crpi-8tnv6lve87c20oxm.cn-beijing.personal.cr.aliyuncs.com`

---

## 第九步：测试验证

完整测试脚本见 [→ 测试流程](./references/test-procedure.md)。

快速验证（服务启动后执行）：

```bash
# 启动容器
docker run -d --name trace-test -p 8081:8080 "$IMAGE"

# 等待健康检查
until [ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8081/actuator/health)" = "200" ]; do sleep 1; done

# 发送追踪请求
TRACE_HDR=$(curl -s -D - -H "X-Return-Trace: true" http://localhost:8081/<any-api-path> \
  | grep -i "x-execution-trace" | awk '{print $2}' | tr -d '\r')

# 解码并打印 spans
python3 -c "
import base64, json, sys
spans = json.loads(base64.b64decode('$TRACE_HDR'))
for s in spans:
    err = '  ← ERROR' if s.get('is_error') else ''
    print(f\"  {s['content']:<40} {s['src_file']}:{s['line_number']}  {s['duration_ns']//1_000_000}ms{err}\")
"

# 清理
docker rm -f trace-test
```

**验收标准**：
- `X-Execution-Trace` 头存在
- spans 数量 ≥ 1
- 每个 span 的 `src_file` 和 `line_number` 非空
- 错误路径的 `is_error` 为 `true`

---

## 已验证的参考实现

`examples/java-microservice/` 是完整的参考实现，包含：
- `tracing/SpanRecord.java`
- `tracing/TraceStore.java`
- `tracing/TraceContextHolder.java`
- `tracing/TraceFilter.java`
- `tracing/TracingAspect.java`（修改版）
- `MicroserviceApplication.java`（含 `@EnableScheduling`）
- `Dockerfile`
