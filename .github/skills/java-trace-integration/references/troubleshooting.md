# 构建与集成故障排除

## Docker 构建常见错误

### `repository name must be lowercase`
**原因**：镜像名含大写字母（如 `PiggyMetrics-gateway`）。  
**修复**：构建时将镜像名转为小写：
```bash
image_name=$(echo "${project}-${module}" | tr '[:upper:]' '[:lower:]')
```

### `FROM java:8-jre: not found`
**原因**：`java:8-jre` 镜像已从 Docker Hub 下架。  
**修复**：替换为：
```
FROM eclipse-temurin:8-jre-focal
# 或 Java 11:
FROM eclipse-temurin:11-jre-jammy
# 或 Java 17:
FROM eclipse-temurin:17-jre-jammy
```

### `/target/xxx.jar: not found`
**原因**：`docker build` 的 build context 不是 Dockerfile 所在目录，导致 `COPY ./target/xxx.jar` 找不到文件。  
**修复**：确保 build context 是 Dockerfile 所在目录：
```bash
docker build -f path/to/Dockerfile -t image-name path/to/dockerfile-dir
```

### `COPY .mvn .mvn 2>/dev/null || true` 报错
**原因**：Dockerfile 中 `COPY` 指令不支持 shell 重定向语法。  
**修复**：直接删除该行，或只在 `.mvn` 目录确实存在时再 COPY。

---

## Maven 编译常见错误

### `package jakarta.servlet does not exist`
**原因**：Spring Boot 2.x 使用 `javax.servlet`，3.x 升级为 `jakarta.servlet`。  
**修复**：
- Spring Boot 2.x → 将 `import jakarta.servlet.*` 改为 `import javax.servlet.*`
- Spring Boot 3.x → 保持 `jakarta.servlet.*` 不变

### `cannot find symbol: record`
**原因**：`record` 是 Java 16+ 新特性，项目使用 Java 11 或更低版本。  
**修复**：将 `TraceStore` 中的 `record Entry(...)` 改为普通内部类：
```java
private static class Entry {
    final long createdAt;
    final List<SpanRecord> spans;
    Entry(long c, List<SpanRecord> s) { this.createdAt = c; this.spans = s; }
}
```

### OTel 相关符号找不到
**原因**：`TracingAspect` 引用了 OTel API，但 `pom.xml` 未添加依赖。  
**修复**：使用 SKILL.md 第二步中的 OTel 依赖，或改用 `references/java-templates.md` 中无 OTel 版本的 `TracingAspect`。

---

## 运行时问题

### `X-Execution-Trace` 头不出现

1. 确认请求携带了 `X-Return-Trace: true` 头
2. 检查 `TraceFilter` 是否被 Spring 扫描到（包路径是否在 `@SpringBootApplication` 覆盖范围内）
3. 检查日志中是否有 `TraceFilter: N spans for trace ...` 输出
4. 确认 `@EnableScheduling` 已添加到主类（否则 `TraceStore` 的 `@Scheduled` 不生效，但不影响追踪本身）

### Spans 为空（0 个）

1. `TracingAspect` 的切入点表达式是否覆盖了实际的 controller/service 包路径
2. Spring AOP 默认只能拦截 Spring Bean 的 public 方法；`private` 或 `static` 方法不被拦截
3. 检查 `pom.xml` 是否有 `spring-boot-starter-aop` 依赖

### `line_number` 全为 -1

**原因**：`LineNumberResolver` 无法从 classfile 读取行号。  
**排查**：确认项目编译时带了 debug 信息（Maven 默认开启，Gradle 需检查 `compileJava.options.debugOptions`）。
