package com.example.microservice.tracing;

import org.objectweb.asm.ClassReader;
import org.objectweb.asm.ClassVisitor;
import org.objectweb.asm.Label;
import org.objectweb.asm.MethodVisitor;
import org.objectweb.asm.Opcodes;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.InputStream;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Resolves the source line number of a Java method by reading the
 * {@code LineNumberTable} attribute that javac writes into every .class file
 * compiled with debug information (the default).
 *
 * Results are cached after the first lookup so the bytecode is only parsed once
 * per method. This works with plain Spring AOP proxy mode — no AspectJ CTW/LTW
 * required.
 */
public class LineNumberResolver {

    private static final Logger log = LoggerFactory.getLogger(LineNumberResolver.class);

    /** Cache key: "com.example.Foo#doSomething" → first line of method body */
    private static final ConcurrentHashMap<String, Integer> CACHE = new ConcurrentHashMap<>();

    private LineNumberResolver() {}

    /**
     * Returns the first line number of {@code methodName} in {@code clazz}
     * by inspecting the compiled bytecode.
     *
     * @return the 1-based line number, or -1 if unavailable
     */
    public static int resolve(Class<?> clazz, String methodName) {
        String key = clazz.getName() + "#" + methodName;
        return CACHE.computeIfAbsent(key, k -> resolveInternal(clazz, methodName));
    }

    private static int resolveInternal(Class<?> clazz, String methodName) {
        String resourceName = clazz.getName().replace('.', '/') + ".class";
        ClassLoader cl = clazz.getClassLoader();
        if (cl == null) cl = ClassLoader.getSystemClassLoader();

        try (InputStream is = cl.getResourceAsStream(resourceName)) {
            if (is == null) {
                log.debug("LineNumberResolver: class resource not found for {}", clazz.getName());
                return -1;
            }

            ClassReader cr = new ClassReader(is);
            int[] result = {-1};

            cr.accept(new ClassVisitor(Opcodes.ASM9) {
                @Override
                public MethodVisitor visitMethod(int access, String name,
                                                  String descriptor, String signature,
                                                  String[] exceptions) {
                    if (!name.equals(methodName)) return null;
                    // Visit only the first matching method name
                    if (result[0] != -1) return null;

                    return new MethodVisitor(Opcodes.ASM9) {
                        @Override
                        public void visitLineNumber(int line, Label start) {
                            // Record the first LineNumber entry = first line of method body
                            if (result[0] == -1) {
                                result[0] = line;
                            }
                        }
                    };
                }
            }, ClassReader.SKIP_FRAMES);

            return result[0];
        } catch (Exception e) {
            log.debug("LineNumberResolver: failed for {}.{}: {}", clazz.getName(), methodName, e.getMessage());
            return -1;
        }
    }
}
