package com.example.tracer;

import com.example.tracer.shaded.asm.*;

import java.lang.instrument.ClassFileTransformer;
import java.security.ProtectionDomain;

/**
 * {@link ClassFileTransformer} that rewrites every method in the target package
 * so that a call to {@link LineRecorder#record(String, String, int)} is inserted
 * before the first instruction that corresponds to each new source line.
 *
 * <p>Only classes whose internal name starts with the configured prefix are
 * instrumented; all others are left untouched (the transformer returns
 * {@code null}).
 */
public class LineTracingTransformer implements ClassFileTransformer {

    private final String targetPrefix; // e.g. "com/example/microservice"

    public LineTracingTransformer(String targetPrefix) {
        this.targetPrefix = targetPrefix.endsWith("/") ? targetPrefix : targetPrefix + "/";
    }

    @Override
    public byte[] transform(ClassLoader loader,
                            String className,
                            Class<?> classBeingRedefined,
                            ProtectionDomain domain,
                            byte[] classfileBuffer) {
        if (className == null || !className.startsWith(targetPrefix)) {
            return null; // leave untouched
        }
        try {
            ClassReader  cr = new ClassReader(classfileBuffer);
            // COMPUTE_FRAMES recalculates stack-map frames after we insert instructions
            ClassWriter  cw = new ClassWriter(cr, ClassWriter.COMPUTE_MAXS | ClassWriter.COMPUTE_FRAMES);
            cr.accept(new LineInjectingClassVisitor(cw, className), ClassReader.EXPAND_FRAMES);
            return cw.toByteArray();
        } catch (Throwable t) {
            // Never crash class loading; silently skip instrumentation
            System.err.println("[line-tracer] failed to instrument " + className + ": " + t);
            return null;
        }
    }

    // ── class visitor ────────────────────────────────────────────────────────

    private static final class LineInjectingClassVisitor extends ClassVisitor {
        private final String internalClassName; // e.g. "com/example/microservice/controller/AppController"

        LineInjectingClassVisitor(ClassVisitor cv, String internalClassName) {
            super(Opcodes.ASM9, cv);
            this.internalClassName = internalClassName;
        }

        @Override
        public MethodVisitor visitMethod(int access, String name, String descriptor,
                                         String signature, String[] exceptions) {
            MethodVisitor mv = super.visitMethod(access, name, descriptor, signature, exceptions);
            // Skip constructors and static initialisers (no interesting source lines)
            if ("<init>".equals(name) || "<clinit>".equals(name)) return mv;
            return new LineInjectingMethodVisitor(mv, internalClassName, name);
        }
    }

    // ── method visitor ───────────────────────────────────────────────────────

    private static final class LineInjectingMethodVisitor extends MethodVisitor {
        private static final String RECORDER_OWNER =
                "com/example/tracer/LineRecorder";
        private static final String RECORDER_DESC  =
                "(Ljava/lang/String;Ljava/lang/String;I)V";

        private final String className;   // internal class name (slashes)
        private final String methodName;

        LineInjectingMethodVisitor(MethodVisitor mv, String className, String methodName) {
            super(Opcodes.ASM9, mv);
            this.className  = className;
            this.methodName = methodName;
        }

        /**
         * Called by ASM for every distinct source line in this method.
         * We emit the original line-number annotation first, then insert
         * a call to {@code LineRecorder.record(className, methodName, line)}.
         */
        @Override
        public void visitLineNumber(int line, Label start) {
            super.visitLineNumber(line, start);

            // Push arguments for LineRecorder.record(String, String, int)
            mv.visitLdcInsn(className.replace('/', '.'));  // String: class FQN
            mv.visitLdcInsn(methodName);                   // String: method name
            // Push int line number (LDC handles any value portably)
            mv.visitLdcInsn(Integer.valueOf(line));
            mv.visitMethodInsn(
                    Opcodes.INVOKESTATIC,
                    RECORDER_OWNER,
                    "record",
                    RECORDER_DESC,
                    false);
        }
    }
}
