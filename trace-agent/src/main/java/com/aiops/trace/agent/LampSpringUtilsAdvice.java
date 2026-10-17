package com.aiops.trace.agent;

import net.bytebuddy.asm.Advice;
import net.bytebuddy.implementation.bytecode.assign.Assigner;

import java.lang.reflect.Method;

public class LampSpringUtilsAdvice {

    @Advice.OnMethodEnter(skipOn = Advice.OnNonDefaultValue.class, suppress = Throwable.class)
    public static boolean onEnter(@Advice.FieldValue(value = "applicationContext", readOnly = false, typing = Assigner.Typing.DYNAMIC) Object ctx,
                                  @Advice.Origin Class<?> clazz,
                                  @Advice.Origin("#m") String methodName) {
        if (ctx == null) {
            // Try 1: use cached ApplicationContext from SpringContextAdvice
            if (SpringContextAdvice.applicationContext != null) {
                ctx = SpringContextAdvice.applicationContext;
                System.out.println("[trace-agent] SpringUtils.applicationContext set from SpringContextAdvice cache");
                return false;
            }

            // Try 2: use RequestContextHolder
            try {
                ClassLoader cl = clazz.getClassLoader();
                if (cl == null) {
                    cl = Thread.currentThread().getContextClassLoader();
                }
                Class<?> holderClass = cl.loadClass("org.springframework.web.context.request.RequestContextHolder");
                Method getAttrs = holderClass.getMethod("getRequestAttributes");
                Object attrs = getAttrs.invoke(null);
                if (attrs != null) {
                    Method getReq = attrs.getClass().getMethod("getRequest");
                    Object req = getReq.invoke(attrs);
                    if (req != null) {
                        Method getServletContext = req.getClass().getMethod("getServletContext");
                        Object sc = getServletContext.invoke(req);
                        if (sc != null) {
                            Class<?> utilsClass = cl.loadClass("org.springframework.web.context.support.WebApplicationContextUtils");
                            Method getWac = utilsClass.getMethod("getWebApplicationContext", cl.loadClass("javax.servlet.ServletContext"));
                            Object wac = getWac.invoke(null, sc);
                            if (wac != null) {
                                ctx = wac;
                                System.out.println("[trace-agent] SpringUtils.applicationContext set from RequestContextHolder");
                                return false;
                            }
                        }
                    }
                }
            } catch (Throwable ignored) {
            }

            // If still null and it is publishEvent, skip execution to avoid NPE
            if ("publishEvent".equals(methodName)) {
                System.out.println("[trace-agent] SpringUtils.publishEvent skipped because applicationContext is null");
                return true;
            }
        }
        return false;
    }

    @Advice.OnMethodExit(suppress = Throwable.class)
    public static void onExit(@Advice.Enter boolean skipped,
                              @Advice.Return(readOnly = false, typing = Assigner.Typing.DYNAMIC) Object returned) {
        if (skipped) {
            returned = null;
        }
    }
}
