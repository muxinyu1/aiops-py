package aiops.tracesmoke.cloud_platform_ace_modules_ace_admin;

import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

@EnableScheduling
@SpringBootApplication(
    scanBasePackages = {"com.github.wxiaoqi.security.tracing", "com.github.wxiaoqi.security.tracesmoke"},
    excludeName = {
        "org.springframework.boot.autoconfigure.jdbc.DataSourceAutoConfiguration",
        "org.springframework.boot.autoconfigure.jdbc.DataSourceTransactionManagerAutoConfiguration",
        "org.springframework.boot.autoconfigure.orm.jpa.HibernateJpaAutoConfiguration",
        "org.springframework.boot.autoconfigure.data.redis.RedisAutoConfiguration",
        "org.springframework.boot.autoconfigure.mongo.MongoAutoConfiguration",
        "org.springframework.boot.autoconfigure.amqp.RabbitAutoConfiguration",
        "org.springframework.boot.autoconfigure.kafka.KafkaAutoConfiguration",
        "org.springframework.boot.autoconfigure.security.servlet.SecurityAutoConfiguration",
        "org.springframework.boot.autoconfigure.security.servlet.SecurityFilterAutoConfiguration",
        "org.springframework.boot.autoconfigure.security.servlet.UserDetailsServiceAutoConfiguration",
        "org.springframework.boot.autoconfigure.security.oauth2.client.servlet.OAuth2ClientAutoConfiguration",
        "org.springframework.boot.autoconfigure.security.oauth2.resource.servlet.OAuth2ResourceServerAutoConfiguration",
        "org.springframework.boot.autoconfigure.security.oauth2.server.servlet.OAuth2AuthorizationServerAutoConfiguration",
        "org.springframework.boot.autoconfigure.security.oauth2.server.servlet.OAuth2AuthorizationServerJwtAutoConfiguration",
        "org.springframework.boot.security.oauth2.server.authorization.autoconfigure.servlet.OAuth2AuthorizationServerAutoConfiguration",
        "org.springframework.boot.security.oauth2.server.authorization.autoconfigure.servlet.OAuth2AuthorizationServerJwtAutoConfiguration",
        "org.springframework.boot.actuate.autoconfigure.security.servlet.ManagementWebSecurityAutoConfiguration",
        "org.springframework.cloud.autoconfigure.LifecycleMvcEndpointAutoConfiguration",
        "org.springframework.cloud.client.CommonsClientAutoConfiguration",
        "org.springframework.cloud.client.ReactiveCommonsClientAutoConfiguration",
        "org.springframework.cloud.client.discovery.composite.CompositeDiscoveryClientAutoConfiguration",
        "org.springframework.cloud.client.discovery.simple.SimpleDiscoveryClientAutoConfiguration",
        "org.springframework.cloud.client.serviceregistry.ServiceRegistryAutoConfiguration",
        "org.springframework.cloud.openfeign.FeignAutoConfiguration",
        "org.springframework.cloud.openfeign.encoding.FeignAcceptGzipEncodingAutoConfiguration",
        "org.springframework.cloud.openfeign.encoding.FeignContentGzipEncodingAutoConfiguration",
        "com.alibaba.cloud.nacos.NacosConfigAutoConfiguration",
        "com.alibaba.cloud.nacos.NacosDiscoveryAutoConfiguration",
        "com.alibaba.cloud.nacos.discovery.NacosDiscoveryAutoConfiguration",
        "com.alibaba.cloud.nacos.registry.NacosServiceRegistryAutoConfiguration",
        "com.alibaba.cloud.sentinel.SentinelWebAutoConfiguration",
        "com.alibaba.cloud.sentinel.feign.SentinelFeignAutoConfiguration",
        "com.pig4cloud.pig.common.feign.PigFeignAutoConfiguration",
        "com.pig4cloud.pig.common.sentinel.PigSentinelAutoConfiguration"
    }
)
public class TraceSmokeApplication {
}
