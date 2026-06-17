package com.mall4j.cloud.common.cache.config;

import com.mall4j.cloud.common.cache.adapter.CacheTtlAdapter;
import com.mall4j.cloud.common.cache.bo.CacheNameWithTtlBO;
import com.mall4j.cloud.common.util.Json;
import org.springframework.boot.autoconfigure.condition.ConditionalOnMissingBean;
import org.springframework.cache.CacheManager;
import org.springframework.cache.annotation.EnableCaching;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.redis.cache.RedisCacheConfiguration;
import org.springframework.data.redis.cache.RedisCacheManager;
import org.springframework.data.redis.cache.RedisCacheWriter;
import org.springframework.data.redis.connection.RedisConnectionFactory;
import org.springframework.data.redis.core.RedisTemplate;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.serializer.GenericJacksonJsonRedisSerializer;
import org.springframework.data.redis.serializer.RedisSerializationContext;
import org.springframework.data.redis.serializer.RedisSerializer;
import org.springframework.data.redis.serializer.StringRedisSerializer;

import java.time.Duration;
import java.util.Collections;
import java.util.HashMap;
import java.util.Map;

/**
 * @author FrozenWatermelon
 * @date 2020/7/4
 */
@EnableCaching
@Configuration
public class RedisCacheConfig {

	@Bean
	public CacheManager cacheManager(RedisConnectionFactory redisConnectionFactory, CacheTtlAdapter cacheTtlAdapter) {

		RedisCacheManager redisCacheManager = new RedisCacheManager(
				RedisCacheWriter.nonLockingRedisCacheWriter(redisConnectionFactory),
				// 默认策略，未配置的 key 会使用这个
				this.getRedisCacheConfigurationWithTtl(3600),
				// 指定 key 策略
				this.getRedisCacheConfigurationMap(cacheTtlAdapter));

		redisCacheManager.setTransactionAware(true);
		return redisCacheManager;
	}

	private Map<String, RedisCacheConfiguration> getRedisCacheConfigurationMap(CacheTtlAdapter cacheTtlAdapter) {
		if (cacheTtlAdapter == null) {
			return Collections.emptyMap();
		}
		Map<String, RedisCacheConfiguration> redisCacheConfigurationMap = new HashMap<>(16);

		for (CacheNameWithTtlBO cacheNameWithTtlBO : cacheTtlAdapter.listCacheNameWithTtl()) {
			redisCacheConfigurationMap.put(cacheNameWithTtlBO.getCacheName(),
					getRedisCacheConfigurationWithTtl(cacheNameWithTtlBO.getTtl()));
		}
		return redisCacheConfigurationMap;
	}

	private RedisCacheConfiguration getRedisCacheConfigurationWithTtl(Integer seconds) {
		RedisCacheConfiguration redisCacheConfiguration = RedisCacheConfiguration.defaultCacheConfig();
		redisCacheConfiguration = redisCacheConfiguration
				.serializeValuesWith(RedisSerializationContext.SerializationPair.fromSerializer(redisSerializer()))
				.entryTtl(Duration.ofSeconds(seconds));

		return redisCacheConfiguration;
	}

	/**
	 * 自定义redis序列化的机制,重新定义一个ObjectMapper.防止和MVC的冲突
	 * <a href="https://juejin.im/post/5e869d426fb9a03c6148c97e">...</a>
	 */
	@Bean
	public RedisSerializer<Object> redisSerializer() {
		// 这里改成 Spring Data Redis 4 提供的 Jackson 3 序列化器，
		// 避免继续使用已经弃用的 GenericJackson2JsonRedisSerializer。
		return new GenericJacksonJsonRedisSerializer(Json.getRedisObjectMapper());
	}

	@Bean
	public RedisTemplate<Object, Object> redisTemplate(RedisConnectionFactory redisConnectionFactory,
			RedisSerializer<Object> redisSerializer) {
		RedisTemplate<Object, Object> template = new RedisTemplate<>();
		template.setConnectionFactory(redisConnectionFactory);
		template.setDefaultSerializer(redisSerializer);
		template.setValueSerializer(redisSerializer);
		template.setHashValueSerializer(redisSerializer);
		template.setKeySerializer(StringRedisSerializer.UTF_8);
		template.setHashKeySerializer(StringRedisSerializer.UTF_8);
		template.afterPropertiesSet();
		return template;
	}

	@Bean
	public StringRedisTemplate stringRedisTemplate(RedisConnectionFactory redisConnectionFactory) {
		return new StringRedisTemplate(redisConnectionFactory);
	}

	@Bean
	@ConditionalOnMissingBean
	public CacheTtlAdapter cacheTtl() {
		return Collections::emptyList;
	}

}
