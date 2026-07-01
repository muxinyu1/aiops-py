package com.aiops.trace.agent;

import net.bytebuddy.agent.builder.AgentBuilder;
import net.bytebuddy.asm.Advice;
import net.bytebuddy.description.type.TypeDescription;
import net.bytebuddy.dynamic.ClassFileLocator;
import net.bytebuddy.dynamic.DynamicType;
import net.bytebuddy.dynamic.loading.ClassInjector;
import net.bytebuddy.matcher.ElementMatchers;
import net.bytebuddy.utility.JavaModule;

import java.lang.instrument.Instrumentation;
import java.security.ProtectionDomain;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;

/**
 * Java Agent entry point.
 *
 * Usage: -javaagent:trace-agent.jar=packages=com.ctrip.framework.apollo,com.macro.mall
 *
 * The agent instruments all public/protected methods in classes under the specified packages,
 * building a full call-stack trace when X-Return-Trace header is present.
 */
public class AgentMain {

    /** Packages to instrument (set at startup, read by matcher). */
    static volatile Set<String> TARGET_PACKAGES = new HashSet<>();

    /** Packages to exclude (framework internals). */
    private static final Set<String> EXCLUDE_PACKAGES = new HashSet<>(Arrays.asList(
            "org.springframework",
            "org.apache",
            "com.zaxxer",
            "com.alibaba.druid",
            "com.baomidou.mybatisplus",
            "net.bytebuddy",
            "com.aiops.trace"
    ));

    /** Class name fragments to exclude (tracing infrastructure from old approach). */
    private static final Set<String> EXCLUDE_CLASSES = new HashSet<>(Arrays.asList(
            "TraceFilter", "TracingAspect", "TraceStore", "TraceContextHolder",
            "LineNumberResolver", "SpanRecord", "TraceSmokeController", "TraceSmokeFilter",
            "TraceSmokeService"
    ));

    public static void premain(String agentArgs, Instrumentation inst) {
        parseArgs(agentArgs);

        System.out.println("[trace-agent] Starting with packages: " + TARGET_PACKAGES);

        // Inject our SpanStackHelper class into the bootstrap classloader
        // so it's visible from all app classloaders.
        try {
            ClassInjector.UsingInstrumentation.of(
                    new java.io.File(AgentMain.class.getProtectionDomain()
                            .getCodeSource().getLocation().toURI()),
                    ClassInjector.UsingInstrumentation.Target.BOOTSTRAP,
                    inst
            ).inject(Collections.singletonMap(
                    new TypeDescription.ForLoadedType(SpanStackHelper.class),
                    ClassFileLocator.ForClassLoader.read(SpanStackHelper.class)
            ));
            System.out.println("[trace-agent] SpanStackHelper injected into bootstrap classloader");
        } catch (Exception e) {
            System.err.println("[trace-agent] WARNING: Could not inject helper: " + e.getMessage());
            e.printStackTrace();
        }

        // Instrument application business methods to capture the full call stack.
        new AgentBuilder.Default()
                .with(AgentBuilder.RedefinitionStrategy.RETRANSFORMATION)
                .type(AgentMain::shouldInstrument)
                .transform(new AgentBuilder.Transformer() {
                    @Override
                    public DynamicType.Builder<?> transform(DynamicType.Builder<?> builder,
                                                            TypeDescription typeDescription,
                                                            ClassLoader classLoader,
                                                            JavaModule module,
                                                            ProtectionDomain protectionDomain) {
                        return builder.visit(
                                Advice.to(MethodTraceAdvice.class)
                                        .on(ElementMatchers.isMethod()
                                                .and(ElementMatchers.not(ElementMatchers.isConstructor()))
                                                .and(ElementMatchers.not(ElementMatchers.isAbstract()))
                                                .and(ElementMatchers.not(ElementMatchers.isSynthetic()))
                                                .and(ElementMatchers.not(ElementMatchers.nameStartsWith("get")))
                                                .and(ElementMatchers.not(ElementMatchers.nameStartsWith("set")))
                                                .and(ElementMatchers.not(ElementMatchers.nameStartsWith("is")))
                                                .and(ElementMatchers.not(ElementMatchers.nameStartsWith("CGLIB$")))
                                                .and(ElementMatchers.not(ElementMatchers.nameContains("$")))
                                                .and(ElementMatchers.not(ElementMatchers.named("toString")))
                                                .and(ElementMatchers.not(ElementMatchers.named("hashCode")))
                                                .and(ElementMatchers.not(ElementMatchers.named("equals")))
                                                .and(ElementMatchers.not(ElementMatchers.named("clone")))
                                                .and(ElementMatchers.not(ElementMatchers.named("finalize")))
                                        )
                        );
                    }
                })
                .with(new AgentBuilder.Listener.Adapter() {
                    @Override
                    public void onError(String typeName, ClassLoader classLoader,
                                        JavaModule module, boolean loaded, Throwable throwable) {
                        // Silently skip classes that can't be instrumented
                    }
                })
                .installOn(inst);

        System.out.println("[trace-agent] Instrumentation installed.");
    }

    private static boolean shouldInstrument(TypeDescription type) {
        String name = type.getName();

        // Exclude framework packages
        for (String excl : EXCLUDE_PACKAGES) {
            if (name.startsWith(excl)) {
                return false;
            }
        }

        // Exclude tracing infrastructure (any class in a .tracing. package or matching known names)
        if (name.contains(".tracing.")) {
            return false;
        }
        // Exclude CGLIB proxy classes (they delegate to real class)
        if (name.contains("$$")) {
            return false;
        }
        String simpleName = name.contains("$") ? name.substring(name.lastIndexOf('$') + 1) : 
                            (name.contains(".") ? name.substring(name.lastIndexOf('.') + 1) : name);
        String outerName = name.contains("$") ? name.substring(name.lastIndexOf('.') + 1, name.indexOf('$')) : simpleName;
        for (String excl : EXCLUDE_CLASSES) {
            if (simpleName.equals(excl) || outerName.equals(excl)) {
                return false;
            }
        }

        // Must match one of the target packages
        for (String pkg : TARGET_PACKAGES) {
            if (name.startsWith(pkg)) {
                return true;
            }
        }
        return false;
    }

    private static void parseArgs(String agentArgs) {
        if (agentArgs == null || agentArgs.isEmpty()) {
            return;
        }
        // Format: packages=pkg1,pkg2,pkg3
        for (String part : agentArgs.split(";")) {
            String[] kv = part.split("=", 2);
            if (kv.length == 2 && "packages".equals(kv[0].trim())) {
                for (String pkg : kv[1].split(",")) {
                    String trimmed = pkg.trim();
                    if (!trimmed.isEmpty()) {
                        TARGET_PACKAGES.add(trimmed);
                    }
                }
            }
        }
    }
}
