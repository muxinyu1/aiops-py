# ── Build stage ────────────────────────────────────────────────────────────────
FROM eclipse-temurin:17-jdk-jammy AS builder
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
FROM eclipse-temurin:17-jre-jammy
WORKDIR /app

# Download OpenTelemetry Java agent for auto-instrumentation + W3C propagation
ARG OTEL_VERSION=2.3.0
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && curl -fSL -o /app/opentelemetry-javaagent.jar \
       "https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/download/v${OTEL_VERSION}/opentelemetry-javaagent.jar" \
    && apt-get purge -y --auto-remove curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /build/target/java-microservice-0.0.1-SNAPSHOT.jar app.jar

EXPOSE 8080

# OTel defaults: export nowhere by default (the app collects traces internally).
# Override OTEL_EXPORTER_OTLP_ENDPOINT at runtime to forward to Jaeger / Collector.
ENV OTEL_SERVICE_NAME=java-microservice \
    OTEL_TRACES_EXPORTER=none \
    OTEL_METRICS_EXPORTER=none \
    OTEL_LOGS_EXPORTER=none \
    JAVA_OPTS=""

# Use exec form via shell so JAVA_OPTS env var is honoured
ENTRYPOINT ["sh", "-c", \
  "exec java $JAVA_OPTS \
    -javaagent:/app/opentelemetry-javaagent.jar \
    -jar /app/app.jar"]
