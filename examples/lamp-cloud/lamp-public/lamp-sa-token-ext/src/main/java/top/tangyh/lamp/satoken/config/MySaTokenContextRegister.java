package top.tangyh.lamp.satoken.config;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import top.tangyh.basic.constant.Constants;
import top.tangyh.lamp.common.properties.IgnoreProperties;
import top.tangyh.lamp.common.properties.SystemProperties;

/**
 * 注册 Sa-Token 框架所需要的 Bean
 * @author tangyh
 * @since 2024/9/18 14:38
 */
@Slf4j
@RequiredArgsConstructor
public class MySaTokenContextRegister {

    @Bean
    public AlwaysConfigurer getAlwaysConfigurer(SystemProperties systemProperties) {
        return new AlwaysConfigurer(systemProperties);
    }


    @Configuration
    @ConditionalOnProperty(prefix = Constants.PROJECT_PREFIX + ".webmvc", name = "header", havingValue = "true", matchIfMissing = true)
    public static class InnerConfig {
        public InnerConfig() {
            log.info("加载：{}", InnerConfig.class.getName());
        }

        @Bean
        public GlobalMvcConfigurer getGlobalMvcConfigurer(IgnoreProperties ignoreProperties) {
            return new GlobalMvcConfigurer();
        }

    }

}
