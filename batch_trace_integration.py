#!/usr/bin/env python3
"""
Batch apply java-trace-integration to all example microservice modules.
Creates tracing classes, modifies the main application class, creates Dockerfile,
then builds and pushes Docker images.
"""
import os
import re
import subprocess
import sys

EXAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "examples")
REGISTRY = "crpi-8tnv6lve87c20oxm.cn-beijing.personal.cr.aliyuncs.com"
NAMESPACE = "llmfuzz"

# ── Target modules ────────────────────────────────────────────────────────────
# (project_dir, module_subdir, root_package, java_version, spring_boot_major)
# spring_boot_major: 2 = javax.servlet, 3 = jakarta.servlet
TARGETS = [
    ("pig", "pig-auth", "com.pig4cloud.pig.auth", 17, 3),
    ("RuoYi-Cloud", "ruoyi-auth", "com.ruoyi.auth", 17, 3),
    ("RuoYi-Cloud-Plus", "ruoyi-auth", "org.dromara.auth", 17, 3),
    ("mall-swarm", "mall-admin", "com.macro.mall", 17, 3),
    ("SpringBlade", "blade-auth", "org.springblade.auth", 17, 3),
    ("youlai-mall", "youlai-auth", "com.youlai.auth", 17, 3),
    ("mall4cloud", "mall4cloud-auth", "com.mall4j.cloud.auth", 17, 3),
    ("zlt-microservices-platform", "zlt-uaa", "com.central", 17, 3),
    ("Apollo", "apollo-adminservice", "com.ctrip.framework.apollo.adminservice", 17, 3),
    ("novel-cloud", "novel-book/novel-book-service", "io.github.xxyopen.novel.book", 17, 3),
    ("yudao-cloud", "yudao-module-system/yudao-module-system-server", "cn.iocoder.yudao.module.system", 8, 2),
    ("PiggyMetrics", "account-service", "com.piggymetrics.account", 8, 2),
]

# ── Templates ─────────────────────────────────────────────────────────────────

def servlet_import(sb_major):
    if sb_major >= 3:
        return "import jakarta.servlet"
    return "import javax.servlet"

def gen_trace_context_holder(pkg):
    return f"""package {pkg}.tracing;

public final class TraceContextHolder {{
    private static final ThreadLocal<String> TRACE_ID = new ThreadLocal<>();
    private TraceContextHolder() {{}}
    public static void set(String traceId)  {{ TRACE_ID.set(traceId); }}
    public static String get()              {{ return TRACE_ID.get(); }}
    public static void clear()              {{ TRACE_ID.remove(); }}
}}
"""

def gen_span_record(pkg):
    return f"""package {pkg}.tracing;

public class SpanRecord {{
    public String span_id;
    public String parent_span_id;
    public String trace_id;
    public String content;
    public String function;
    public String method_signature;
    public String class_namespace;
    public String src_file;
    public int    line_number;
    public long   start_ns;
    public long   duration_ns;
    public boolean is_error;
    public String  error_message;
}}
"""

def gen_trace_store(pkg, java_ver):
    # Java 16+ can use record; older uses static class
    if java_ver >= 16:
        entry_def = "    private record Entry(long createdAt, List<SpanRecord> spans) {}"
    else:
        entry_def = """    private static class Entry {
        final long createdAt;
        final List<SpanRecord> spans;
        Entry(long createdAt, List<SpanRecord> spans) {
            this.createdAt = createdAt; this.spans = spans;
        }
        long createdAt() { return createdAt; }
        List<SpanRecord> spans() { return spans; }
    }"""
    return f"""package {pkg}.tracing;

import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.*;
import java.util.concurrent.ConcurrentHashMap;

@Component
public class TraceStore {{

    private static final long TTL_MS = 5 * 60_000L;

{entry_def}

    private final ConcurrentHashMap<String, Entry> store = new ConcurrentHashMap<>();

    public void add(String traceId, SpanRecord record) {{
        store.computeIfAbsent(traceId, k ->
            new Entry(System.currentTimeMillis(),
                      Collections.synchronizedList(new ArrayList<>()))
        ).spans().add(record);
    }}

    public List<SpanRecord> getAndRemove(String traceId) {{
        Entry entry = store.remove(traceId);
        return entry == null ? Collections.emptyList() : new ArrayList<>(entry.spans());
    }}

    @Scheduled(fixedDelay = 60_000)
    public void cleanup() {{
        long cutoff = System.currentTimeMillis() - TTL_MS;
        store.entrySet().removeIf(e -> e.getValue().createdAt() < cutoff);
    }}
}}
"""

def gen_trace_filter(pkg, sb_major):
    servlet_pkg = "jakarta.servlet" if sb_major >= 3 else "javax.servlet"
    return f"""package {pkg}.tracing;

import com.fasterxml.jackson.databind.ObjectMapper;
import {servlet_pkg}.FilterChain;
import {servlet_pkg}.ServletException;
import {servlet_pkg}.http.HttpServletRequest;
import {servlet_pkg}.http.HttpServletResponse;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import org.springframework.web.util.ContentCachingResponseWrapper;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.List;
import java.util.UUID;

@Component
public class TraceFilter extends OncePerRequestFilter {{

    @Autowired private TraceStore traceStore;
    @Autowired private ObjectMapper objectMapper;

    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                    HttpServletResponse response,
                                    FilterChain chain)
            throws ServletException, IOException {{

        boolean wantTrace = "true".equalsIgnoreCase(request.getHeader("X-Return-Trace"));
        if (!wantTrace) {{
            chain.doFilter(request, response);
            return;
        }}

        String traceId = extractTraceId(request.getHeader("traceparent"));
        if (traceId == null) traceId = UUID.randomUUID().toString().replace("-", "");
        TraceContextHolder.set(traceId);

        ContentCachingResponseWrapper wrappedResponse =
                new ContentCachingResponseWrapper(response);
        try {{
            chain.doFilter(request, wrappedResponse);
        }} finally {{
            try {{
                List<SpanRecord> spans = traceStore.getAndRemove(traceId);
                if (!spans.isEmpty()) {{
                    String json    = objectMapper.writeValueAsString(spans);
                    String encoded = Base64.getEncoder()
                            .encodeToString(json.getBytes(StandardCharsets.UTF_8));
                    wrappedResponse.setHeader("X-Execution-Trace", encoded);
                }}
            }} catch (Exception e) {{
                // ignore serialization errors
            }} finally {{
                TraceContextHolder.clear();
            }}
            wrappedResponse.copyBodyToResponse();
        }}
    }}

    private static String extractTraceId(String traceparent) {{
        if (traceparent == null) return null;
        String[] parts = traceparent.split("-", -1);
        return (parts.length >= 4 && parts[1].length() == 32) ? parts[1] : null;
    }}
}}
"""

def gen_line_number_resolver(pkg):
    return f"""package {pkg}.tracing;

import org.objectweb.asm.ClassReader;
import org.objectweb.asm.ClassVisitor;
import org.objectweb.asm.Label;
import org.objectweb.asm.MethodVisitor;
import org.objectweb.asm.Opcodes;

import java.io.InputStream;
import java.util.concurrent.ConcurrentHashMap;

public class LineNumberResolver {{

    private static final ConcurrentHashMap<String, Integer> CACHE = new ConcurrentHashMap<>();

    private LineNumberResolver() {{}}

    public static int resolve(Class<?> clazz, String methodName) {{
        String key = clazz.getName() + "#" + methodName;
        return CACHE.computeIfAbsent(key, k -> resolveInternal(clazz, methodName));
    }}

    private static int resolveInternal(Class<?> clazz, String methodName) {{
        String resourceName = clazz.getName().replace('.', '/') + ".class";
        ClassLoader cl = clazz.getClassLoader();
        if (cl == null) cl = ClassLoader.getSystemClassLoader();

        try (InputStream is = cl.getResourceAsStream(resourceName)) {{
            if (is == null) return -1;
            ClassReader cr = new ClassReader(is);
            int[] result = {{-1}};
            cr.accept(new ClassVisitor(Opcodes.ASM9) {{
                @Override
                public MethodVisitor visitMethod(int access, String name,
                                                  String descriptor, String signature,
                                                  String[] exceptions) {{
                    if (!name.equals(methodName)) return null;
                    if (result[0] != -1) return null;
                    return new MethodVisitor(Opcodes.ASM9) {{
                        @Override
                        public void visitLineNumber(int line, Label start) {{
                            if (result[0] == -1) result[0] = line;
                        }}
                    }};
                }}
            }}, ClassReader.SKIP_FRAMES);
            return result[0];
        }} catch (Exception e) {{
            return -1;
        }}
    }}
}}
"""

def gen_tracing_aspect(pkg):
    return f"""package {pkg}.tracing;

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
public class TracingAspect {{

    @Autowired
    private TraceStore traceStore;

    @Around("execution(* {pkg}..*(..))")
    public Object traceMethod(ProceedingJoinPoint pjp) throws Throwable {{

        String traceId = TraceContextHolder.get();
        if (traceId == null) {{
            return pjp.proceed();
        }}

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

        try {{
            return pjp.proceed();
        }} catch (Throwable ex) {{
            isError  = true;
            errorMsg = ex.getMessage();
            throw ex;
        }} finally {{
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
        }}
    }}
}}
"""

def gen_dockerfile(java_ver, jar_name, module_path=""):
    """Generate a Dockerfile. For multi-module projects, it's placed at
    project root and builds a specific module."""
    jdk_tag = f"{java_ver}-jdk-jammy" if java_ver >= 11 else "8-jdk-focal"
    jre_tag = f"{java_ver}-jre-jammy" if java_ver >= 11 else "8-jre-focal"
    if module_path:
        # Multi-module: build from project root, target specific module
        return f"""FROM eclipse-temurin:{jdk_tag} AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends maven && rm -rf /var/lib/apt/lists/*
COPY . .
RUN mvn clean package -DskipTests -pl {module_path} -am -q

FROM eclipse-temurin:{jre_tag}
WORKDIR /app
ARG OTEL_VERSION=2.3.0
RUN apt-get update && apt-get install -y --no-install-recommends curl \\
    && curl -fSL -o /app/opentelemetry-javaagent.jar \\
       "https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/download/v${{OTEL_VERSION}}/opentelemetry-javaagent.jar" \\
    && apt-get purge -y --auto-remove curl && rm -rf /var/lib/apt/lists/*
COPY --from=builder /build/{module_path}/target/{jar_name} app.jar
EXPOSE 8080
ENV OTEL_SERVICE_NAME=microservice OTEL_TRACES_EXPORTER=none OTEL_METRICS_EXPORTER=none OTEL_LOGS_EXPORTER=none JAVA_OPTS=""
ENTRYPOINT ["sh", "-c", "exec java $JAVA_OPTS -javaagent:/app/opentelemetry-javaagent.jar -jar /app/app.jar"]
"""
    else:
        # Single-module (standalone)
        return f"""FROM eclipse-temurin:{jdk_tag} AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends maven && rm -rf /var/lib/apt/lists/*
COPY pom.xml ./
RUN mvn dependency:go-offline -q 2>/dev/null || true
COPY src ./src
RUN mvn clean package -DskipTests -q

FROM eclipse-temurin:{jre_tag}
WORKDIR /app
ARG OTEL_VERSION=2.3.0
RUN apt-get update && apt-get install -y --no-install-recommends curl \\
    && curl -fSL -o /app/opentelemetry-javaagent.jar \\
       "https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/download/v${{OTEL_VERSION}}/opentelemetry-javaagent.jar" \\
    && apt-get purge -y --auto-remove curl && rm -rf /var/lib/apt/lists/*
COPY --from=builder /build/target/{jar_name} app.jar
EXPOSE 8080
ENV OTEL_SERVICE_NAME=microservice OTEL_TRACES_EXPORTER=none OTEL_METRICS_EXPORTER=none OTEL_LOGS_EXPORTER=none JAVA_OPTS=""
ENTRYPOINT ["sh", "-c", "exec java $JAVA_OPTS -javaagent:/app/opentelemetry-javaagent.jar -jar /app/app.jar"]
"""

# ── Helper functions ──────────────────────────────────────────────────────────

def get_module_dir(project, module):
    return os.path.join(EXAMPLES, project, module)

def get_tracing_dir(module_dir, root_pkg):
    pkg_path = root_pkg.replace(".", "/")
    return os.path.join(module_dir, "src/main/java", pkg_path, "tracing")

def find_jar_name(module_dir):
    """Extract artifactId-version from pom.xml to predict jar name."""
    pom = os.path.join(module_dir, "pom.xml")
    if not os.path.exists(pom):
        return "app.jar"
    content = open(pom).read()
    # Try artifactId
    m_art = re.search(r"<artifactId>([^<]+)</artifactId>", content)
    m_ver = re.search(r"<version>([^<]+)</version>", content)
    if m_art and m_ver:
        return f"{m_art.group(1)}-{m_ver.group(1)}.jar"
    return "*.jar"

def ensure_deps_in_pom(module_dir):
    """Add spring-boot-starter-aop and asm deps if missing."""
    pom = os.path.join(module_dir, "pom.xml")
    if not os.path.exists(pom):
        return
    content = open(pom).read()
    deps_to_add = []
    if "spring-boot-starter-aop" not in content:
        deps_to_add.append("""        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-aop</artifactId>
        </dependency>""")
    if "org.ow2.asm" not in content and "<artifactId>asm</artifactId>" not in content:
        deps_to_add.append("""        <dependency>
            <groupId>org.ow2.asm</groupId>
            <artifactId>asm</artifactId>
            <version>9.7</version>
        </dependency>""")
    if not deps_to_add:
        return
    # Insert before </dependencies>
    insert = "\n".join(deps_to_add) + "\n"
    content = content.replace("</dependencies>", insert + "    </dependencies>", 1)
    open(pom, "w").write(content)
    print(f"    [pom] added {len(deps_to_add)} dependencies")

def add_enable_scheduling(module_dir, root_pkg):
    """Add @EnableScheduling to the main application class."""
    java_dir = os.path.join(module_dir, "src/main/java")
    for root, dirs, files in os.walk(java_dir):
        for f in files:
            if f.endswith(".java"):
                path = os.path.join(root, f)
                content = open(path).read()
                if "@SpringBootApplication" in content and "@EnableScheduling" not in content:
                    content = content.replace(
                        "@SpringBootApplication",
                        "@EnableScheduling\n@SpringBootApplication"
                    )
                    if "import org.springframework.scheduling.annotation.EnableScheduling" not in content:
                        content = content.replace(
                            "import org.springframework.boot",
                            "import org.springframework.scheduling.annotation.EnableScheduling;\nimport org.springframework.boot",
                            1
                        )
                    open(path, "w").write(content)
                    print(f"    [main] added @EnableScheduling to {f}")
                    return

def process_module(project, module, root_pkg, java_ver, sb_major):
    print(f"\n{'='*60}")
    print(f"  Processing: {project}/{module}")
    print(f"  Package: {root_pkg}, Java: {java_ver}, SB: {sb_major}.x")
    print(f"{'='*60}")

    module_dir = get_module_dir(project, module)
    if not os.path.exists(module_dir):
        print(f"  [SKIP] module dir not found: {module_dir}")
        return False

    tracing_dir = get_tracing_dir(module_dir, root_pkg)
    os.makedirs(tracing_dir, exist_ok=True)

    # 1. Create tracing classes
    files = {
        "TraceContextHolder.java": gen_trace_context_holder(root_pkg),
        "SpanRecord.java": gen_span_record(root_pkg),
        "TraceStore.java": gen_trace_store(root_pkg, java_ver),
        "TraceFilter.java": gen_trace_filter(root_pkg, sb_major),
        "LineNumberResolver.java": gen_line_number_resolver(root_pkg),
        "TracingAspect.java": gen_tracing_aspect(root_pkg),
    }
    for fname, content in files.items():
        fpath = os.path.join(tracing_dir, fname)
        if not os.path.exists(fpath):
            open(fpath, "w").write(content)
            print(f"    [create] {fname}")
        else:
            print(f"    [skip] {fname} already exists")

    # 2. Add Maven dependencies
    ensure_deps_in_pom(module_dir)

    # 3. Add @EnableScheduling
    add_enable_scheduling(module_dir, root_pkg)

    # 4. Create Dockerfile at project root (multi-module) or module dir (standalone)
    project_dir = os.path.join(EXAMPLES, project)
    is_multi_module = (module != "" and "/" not in module and
                       os.path.exists(os.path.join(project_dir, "pom.xml")) and
                       module_dir != project_dir)
    # For nested modules like "novel-book/novel-book-service", check parent
    if "/" in module:
        is_multi_module = True

    dockerfile_dir = project_dir if is_multi_module else module_dir
    dockerfile = os.path.join(dockerfile_dir, f"Dockerfile.{module.split('/')[-1]}")
    if is_multi_module:
        # Use a per-module Dockerfile at project root
        if not os.path.exists(dockerfile):
            jar_name = find_jar_name(module_dir)
            open(dockerfile, "w").write(gen_dockerfile(java_ver, jar_name, module))
            print(f"    [create] Dockerfile.{module.split('/')[-1]} at project root (jar={jar_name})")
        else:
            print(f"    [skip] Dockerfile.{module.split('/')[-1]} already exists")
    else:
        dockerfile = os.path.join(module_dir, "Dockerfile")
        if not os.path.exists(dockerfile):
            jar_name = find_jar_name(module_dir)
            open(dockerfile, "w").write(gen_dockerfile(java_ver, jar_name))
            print(f"    [create] Dockerfile (jar={jar_name})")
        else:
            print(f"    [skip] Dockerfile already exists")

    return True


def build_and_push(project, module):
    project_dir = os.path.join(EXAMPLES, project)
    module_dir = get_module_dir(project, module)
    image_name = f"{REGISTRY}/{NAMESPACE}/{project.lower()}-{module.split('/')[-1].lower()}:latest"

    # Determine build context and dockerfile
    is_multi_module = (module != "" and os.path.exists(os.path.join(project_dir, "pom.xml")) and
                       module_dir != project_dir)
    if "/" in module:
        is_multi_module = True

    if is_multi_module:
        build_ctx = project_dir
        dockerfile = os.path.join(project_dir, f"Dockerfile.{module.split('/')[-1]}")
    else:
        build_ctx = module_dir
        dockerfile = os.path.join(module_dir, "Dockerfile")

    print(f"\n  Building: {image_name}")
    print(f"    context: {build_ctx}")
    print(f"    dockerfile: {dockerfile}")

    result = subprocess.run(
        ["docker", "build", "-f", dockerfile, "-t", image_name, "."],
        cwd=build_ctx,
        capture_output=True, text=True, timeout=1800
    )
    if result.returncode != 0:
        # Print last 500 chars of stderr
        err = result.stderr[-500:] if result.stderr else result.stdout[-500:]
        print(f"  [BUILD FAILED] {err}")
        return False

    print(f"  [BUILD OK] Pushing...")
    result = subprocess.run(
        ["docker", "push", image_name],
        capture_output=True, text=True, timeout=600
    )
    if result.returncode != 0:
        print(f"  [PUSH FAILED] {result.stderr[-300:]}")
        return False

    print(f"  [PUSHED] {image_name}")
    return True


def main():
    # Allow filtering by project name via CLI arg
    filter_project = sys.argv[1] if len(sys.argv) > 1 else None

    results = {"ok": [], "fail": [], "skip": []}

    for project, module, root_pkg, java_ver, sb_major in TARGETS:
        if filter_project and filter_project != project:
            continue

        ok = process_module(project, module, root_pkg, java_ver, sb_major)
        if not ok:
            results["skip"].append(f"{project}/{module}")
            continue

        if build_and_push(project, module):
            results["ok"].append(f"{project}/{module}")
        else:
            results["fail"].append(f"{project}/{module}")

    print("\n\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    print(f"  Success: {len(results['ok'])}")
    for x in results["ok"]:
        print(f"    ✅ {x}")
    print(f"  Failed:  {len(results['fail'])}")
    for x in results["fail"]:
        print(f"    ❌ {x}")
    print(f"  Skipped: {len(results['skip'])}")
    for x in results["skip"]:
        print(f"    ⏭  {x}")


if __name__ == "__main__":
    main()
