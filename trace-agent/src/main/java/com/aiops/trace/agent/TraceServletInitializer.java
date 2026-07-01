package com.aiops.trace.agent;

import javax.servlet.FilterRegistration;
import javax.servlet.ServletContainerInitializer;
import javax.servlet.ServletContext;
import javax.servlet.ServletException;
import java.util.EnumSet;
import java.util.Set;

/**
 * Registers the TraceFilter with the highest priority in the Servlet container.
 * This is discovered automatically via META-INF/services/javax.servlet.ServletContainerInitializer.
 */
public class TraceServletInitializer implements ServletContainerInitializer {

    @Override
    public void onStartup(Set<Class<?>> c, ServletContext ctx) throws ServletException {
        System.out.println("[trace-agent] Registering TraceFilter via ServletContainerInitializer");

        FilterRegistration.Dynamic registration = ctx.addFilter("traceAgentFilter", new TraceFilter());
        if (registration != null) {
            registration.addMappingForUrlPatterns(
                    EnumSet.of(javax.servlet.DispatcherType.REQUEST),
                    false, // isMatchAfter=false means highest priority
                    "/*"
            );
            registration.setAsyncSupported(true);
            System.out.println("[trace-agent] TraceFilter registered successfully");
        } else {
            System.out.println("[trace-agent] TraceFilter already registered (skipped)");
        }
    }
}
