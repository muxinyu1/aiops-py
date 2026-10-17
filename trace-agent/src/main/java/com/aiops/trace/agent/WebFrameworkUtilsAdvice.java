package com.aiops.trace.agent;

import net.bytebuddy.asm.Advice;
import net.bytebuddy.implementation.bytecode.assign.Assigner;

import java.lang.reflect.Method;

/**
 * Advice to auto-initialize WebFrameworkUtils.properties if null,
 * resolving the Yudao NPE in WebFrameworkUtils.getLoginUserType.
 */
public class WebFrameworkUtilsAdvice {

    @Advice.OnMethodEnter(suppress = Throwable.class)
    public static void onEnter(@Advice.FieldValue(value = "properties", readOnly = false, typing = Assigner.Typing.DYNAMIC) Object props,
                               @Advice.Origin Class<?> clazz) {
        if (props == null) {
            try {
                ClassLoader cl = clazz.getClassLoader();
                if (cl == null) {
                    cl = Thread.currentThread().getContextClassLoader();
                }
                Class<?> propClass = cl.loadClass("cn.iocoder.yudao.framework.web.config.WebProperties");
                Object newProps = propClass.newInstance();

                try {
                    Class<?> uiClass = cl.loadClass("cn.iocoder.yudao.framework.web.config.WebProperties$Ui");
                    Object ui = uiClass.newInstance();
                    Method setUrl = uiClass.getMethod("setUrl", String.class);
                    setUrl.invoke(ui, "http://127.0.0.1");
                    Method setAdminUi = propClass.getMethod("setAdminUi", uiClass);
                    setAdminUi.invoke(newProps, ui);
                } catch (Throwable ignored) {
                }

                props = newProps;
                System.out.println("[trace-agent] WebFrameworkUtils.properties initialized successfully via WebFrameworkUtilsAdvice");
            } catch (Throwable t) {
                System.err.println("[trace-agent] Failed to initialize WebFrameworkUtils.properties: " + t.getMessage());
            }
        }
    }
}
