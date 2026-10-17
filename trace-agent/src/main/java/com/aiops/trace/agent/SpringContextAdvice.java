package com.aiops.trace.agent;

import net.bytebuddy.asm.Advice;
import java.lang.reflect.Method;

public class SpringContextAdvice {

    public static volatile Object applicationContext = null;

    @Advice.OnMethodExit(suppress = Throwable.class)
    public static void onExit(@Advice.This Object context) {
        if (context == null) return;
        applicationContext = context;
        try {
            ClassLoader cl = context.getClass().getClassLoader();
            if (cl == null) {
                cl = Thread.currentThread().getContextClassLoader();
            }
            Class<?> springUtilsClass = cl.loadClass("top.tangyh.basic.utils.SpringUtils");
            Class<?> appContextClass = cl.loadClass("org.springframework.context.ApplicationContext");
            Method setContextMethod = springUtilsClass.getMethod("setApplicationContext", appContextClass);
            setContextMethod.invoke(null, context);
            System.out.println("[trace-agent] SpringUtils.setApplicationContext initialized via AbstractApplicationContext.finishRefresh!");
        } catch (Throwable t) {
            // Not a lamp app or SpringUtils not present, ignore
        }
    }
}
