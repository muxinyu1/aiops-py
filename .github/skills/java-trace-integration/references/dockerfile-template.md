# Dockerfile 模板

## 多阶段构建模板

将 `<JAVA_VERSION>`（如 `17`）和 `<JAR_NAME>`（如 `my-service-0.0.1-SNAPSHOT.jar`）替换为实际值。

```dockerfile
# ── Build stage ────────────────────────────────────────────────────────────────
FROM eclipse-temurin:<JAVA_VERSION>-jdk-jammy AS builder
WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends maven \
    && rm -rf /var/lib/apt/lists/*

# Copy POM first for dependency-layer caching
COPY pom.xml ./
RUN mvn dependency:go-offline -q 2>/dev/null || true

# Build
COPY src ./src
RUN mvn clean package -DskipTests -q

# ── Runtime stage ──────────────────────────────────────────────────────────────
FROM eclipse-temurin:<JAVA_VERSION>-jre-jammy
WORKDIR /app

# Download OpenTelemetry Java agent
ARG OTEL_VERSION=2.3.0
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && curl -fSL -o /app/opentelemetry-javaagent.jar \
       "https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/download/v${OTEL_VERSION}/opentelemetry-javaagent.jar" \
    && apt-get purge -y --auto-remove curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /build/target/<JAR_NAME> app.jar

EXPOSE 8080

ENV OTEL_SERVICE_NAME=<service-name> \
    OTEL_TRACES_EXPORTER=none \
    OTEL_METRICS_EXPORTER=none \
    OTEL_LOGS_EXPORTER=none \
    JAVA_OPTS=""

ENTRYPOINT ["sh", "-c", \
  "exec java $JAVA_OPTS \
    -javaagent:/app/opentelemetry-javaagent.jar \
    -jar /app/app.jar"]
```

---

## 变量确认清单

| 变量 | 如何确认 | 示例 |
|------|----------|------|
| `<JAVA_VERSION>` | `pom.xml` → `<java.version>` 或 `<maven.compiler.source>` | `17` |
| `<JAR_NAME>` | `pom.xml` → `<artifactId>-<version>.jar`（通常 SNAPSHOT） | `piggymetrics-1.0-SNAPSHOT.jar` |
| `<service-name>` | 服务简称，全小写 | `piggymetrics-gateway` |

---

## 故障排除 {#troubleshooting}

见 [troubleshooting.md](./troubleshooting.md)。
