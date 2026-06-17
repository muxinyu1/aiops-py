package com.mall4j.cloud.auth.tracing;

import org.objectweb.asm.ClassReader;
import org.objectweb.asm.ClassVisitor;
import org.objectweb.asm.Label;
import org.objectweb.asm.MethodVisitor;
import org.objectweb.asm.Opcodes;

import java.io.InputStream;
import java.util.concurrent.ConcurrentHashMap;

public class LineNumberResolver {

    private static final ConcurrentHashMap<String, Integer> CACHE = new ConcurrentHashMap<>();

    private LineNumberResolver() {}

    public static int resolve(Class<?> clazz, String methodName) {
        String key = clazz.getName() + "#" + methodName;
        return CACHE.computeIfAbsent(key, k -> resolveInternal(clazz, methodName));
    }

    private static int resolveInternal(Class<?> clazz, String methodName) {
        String resourceName = clazz.getName().replace('.', '/') + ".class";
        ClassLoader cl = clazz.getClassLoader();
        if (cl == null) cl = ClassLoader.getSystemClassLoader();

        try (InputStream is = cl.getResourceAsStream(resourceName)) {
            if (is == null) return -1;
            ClassReader cr = new ClassReader(is);
            int[] result = {-1};
            cr.accept(new ClassVisitor(Opcodes.ASM9) {
                @Override
                public MethodVisitor visitMethod(int access, String name,
                                                  String descriptor, String signature,
                                                  String[] exceptions) {
                    if (!name.equals(methodName)) return null;
                    if (result[0] != -1) return null;
                    return new MethodVisitor(Opcodes.ASM9) {
                        @Override
                        public void visitLineNumber(int line, Label start) {
                            if (result[0] == -1) result[0] = line;
                        }
                    };
                }
            }, ClassReader.SKIP_FRAMES);
            return result[0];
        } catch (Exception e) {
            return -1;
        }
    }
}
