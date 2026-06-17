package com.mall4j.cloud.common.feign;

import org.apache.hc.client5.http.config.RequestConfig;
import org.apache.hc.client5.http.impl.classic.CloseableHttpClient;
import org.apache.hc.client5.http.impl.classic.HttpClientBuilder;
import org.apache.hc.client5.http.impl.io.PoolingHttpClientConnectionManager;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.util.concurrent.TimeUnit;

/**
 * @author FrozenWatermelon
 * @date 2020/12/11
 */
@Configuration
public class FeignHttpClientConfig {

    @Bean(destroyMethod = "close")
    public CloseableHttpClient httpClient() {
        PoolingHttpClientConnectionManager connectionManager = new PoolingHttpClientConnectionManager();
        connectionManager.setMaxTotal(400);
        connectionManager.setDefaultMaxPerRoute(100);

        RequestConfig requestConfig = RequestConfig.custom()
                //从连接池获取连接等待超时时间
                .setConnectionRequestTimeout(2000, TimeUnit.MILLISECONDS)
                //请求超时时间
                .setConnectTimeout(2000, TimeUnit.MILLISECONDS)
                //等待服务响应超时时间
                .setResponseTimeout(15000, TimeUnit.MILLISECONDS)
                .build();
        HttpClientBuilder httpClientBuilder = HttpClientBuilder.create().setConnectionManager(connectionManager)
                .setDefaultRequestConfig(requestConfig)
                .evictExpiredConnections();
        return httpClientBuilder.build();
    }
}
