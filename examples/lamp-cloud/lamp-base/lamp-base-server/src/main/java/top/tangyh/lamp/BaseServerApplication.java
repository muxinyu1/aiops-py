package top.tangyh.lamp;

import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.cloud.client.discovery.EnableDiscoveryClient;
import org.springframework.cloud.openfeign.EnableFeignClients;
import org.springframework.context.annotation.ComponentScan;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.EnableAspectJAutoProxy;
import top.tangyh.basic.validator.annotation.EnableFormValidator;
import top.tangyh.lamp.common.ServerApplication;

import java.net.UnknownHostException;

import static top.tangyh.lamp.common.constant.BizConstant.BUSINESS_PACKAGE;
import static top.tangyh.lamp.common.constant.BizConstant.UTIL_PACKAGE;

/**
 * 基础服务启动类
 *
 * @author zuihou
 * @date 2021-10-08
 */
@SpringBootApplication
@EnableDiscoveryClient
@Configuration
@ComponentScan({
        UTIL_PACKAGE, BUSINESS_PACKAGE
})
@EnableFeignClients(value = {
        UTIL_PACKAGE, BUSINESS_PACKAGE
})
@EnableAspectJAutoProxy(proxyTargetClass = true, exposeProxy = true)
@Slf4j
@EnableFormValidator
public class BaseServerApplication extends ServerApplication {
    public static void main(String[] args) throws UnknownHostException {
        start(BaseServerApplication.class, args);
    }
}
