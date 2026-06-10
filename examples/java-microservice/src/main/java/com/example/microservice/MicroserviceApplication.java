package com.example.microservice;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.ConfigurableApplicationContext;

@SpringBootApplication
public class MicroserviceApplication {

    private static final Logger log = LoggerFactory.getLogger(MicroserviceApplication.class);

    public static void main(String[] args) {
        log.info("Starting Java Microservice...");
        ConfigurableApplicationContext ctx = SpringApplication.run(MicroserviceApplication.class, args);
        String port = ctx.getEnvironment().getProperty("local.server.port", "8080");
        log.info("Java Microservice started on port {}", port);
    }
}
