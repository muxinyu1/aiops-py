package top.tangyh.lamp.satoken.config;

import lombok.RequiredArgsConstructor;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;
import top.tangyh.lamp.satoken.interceptor.HeaderThreadLocalInterceptor;

/**
 *  单体模式不执行的类
 *
 * @author zuihou
 * @date 2018/8/25
 */
@RequiredArgsConstructor
public class GlobalMvcConfigurer implements WebMvcConfigurer {

    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        registry.addInterceptor(new HeaderThreadLocalInterceptor())
                .addPathPatterns("/**")
                .order(-20);
    }
}
