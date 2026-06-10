package com.example.tracer;

import java.io.File;
import java.lang.instrument.Instrumentation;
import java.net.URI;
import java.util.jar.JarFile;

/**
 * Java agent entry point.
 *
 * <p>Launch with:
 * <pre>
 *   -javaagent:/path/to/line-tracer-agent-0.0.1.jar=\
 *       package=com/example/microservice,\
 *       endpoint=http://localhost:4318/v1/lines
 * </pre>
 *
 * <p>The agent adds itself to the <em>bootstrap</em> class-loader so that
 * instrumented application classes (which may use a child class-loader such as
 * Spring Boot's {@code LaunchedURLClassLoader}) can resolve the
 * {@link LineRecorder} class that is injected into their bytecode.
 */
public class LineTracerAgent {

    public static void premain(String args, Instrumentation inst) throws Exception {
        // ── parse agent arguments ────────────────────────────────────────────
        String targetPackagePrefix = "com/example/microservice";
        String endpoint            = "http://localhost:4318/v1/lines";

        if (args != null && !args.isBlank()) {
            for (String kv : args.split(",")) {
                String[] parts = kv.split("=", 2);
                if (parts.length == 2) {
                    switch (parts[0].trim()) {
                        case "package"  -> targetPackagePrefix = parts[1].trim();
                        case "endpoint" -> endpoint            = parts[1].trim();
                    }
                }
            }
        }

        // ── add this agent JAR to the bootstrap class-loader ─────────────────
        // This makes LineRecorder (and the shaded ASM classes) visible to ALL
        // class-loaders, including Spring Boot's LaunchedURLClassLoader, so
        // that the INVOKESTATIC inserted into application bytecode can resolve.
        URI selfUri = LineTracerAgent.class
                .getProtectionDomain().getCodeSource().getLocation().toURI();
        File selfJar = new File(selfUri.getPath());
        inst.appendToBootstrapClassLoaderSearchPath(new JarFile(selfJar));

        // ── initialise the recorder and install the transformer ───────────────
        LineRecorder.init(endpoint);
        inst.addTransformer(new LineTracingTransformer(targetPackagePrefix));

        System.err.println("[line-tracer] agent started  package=" + targetPackagePrefix
                + "  endpoint=" + endpoint);
    }
}
