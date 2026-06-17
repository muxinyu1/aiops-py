package com.mall4j.cloud.common.util;

import com.fasterxml.jackson.annotation.JsonAutoDetect;
import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonTypeInfo;
import tools.jackson.databind.*;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.cfg.EnumFeature;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.jsontype.BasicPolymorphicTypeValidator;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;

/**
 * @author FrozenWatermelon
 * @date 2020/7/11
 */
public class Json {

	private static final Logger logger = LoggerFactory.getLogger(Json.class);

	public static JsonMapper.Builder newBaseBuilder() {
		return JsonMapper.builder()
				.disable(SerializationFeature.FAIL_ON_EMPTY_BEANS)
				.disable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
				.disable(SerializationFeature.FAIL_ON_SELF_REFERENCES)
				.enable(SerializationFeature.WRITE_SELF_REFERENCES_AS_NULL)
				.disable(EnumFeature.FAIL_ON_NUMBERS_FOR_ENUMS)
				.changeDefaultVisibility(vc -> vc
						.withFieldVisibility(JsonAutoDetect.Visibility.ANY)
						.withGetterVisibility(JsonAutoDetect.Visibility.NONE)
						.withIsGetterVisibility(JsonAutoDetect.Visibility.NONE)
						.withSetterVisibility(JsonAutoDetect.Visibility.NONE)
						.withCreatorVisibility(JsonAutoDetect.Visibility.NONE));
		// 如果你想尽量贴近 Jackson 2 默认行为，可再补：
		// .disable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS)
	}

	/**
	 * 普通用途：深拷贝、parseObject、mapAsList
	 */
	private static final ObjectMapper OBJECT_MAPPER =
			newBaseBuilder().build();

	/**
	 * Redis 专用：支持多态
	 */
	private static final ObjectMapper REDIS_OBJECT_MAPPER =
			newBaseBuilder()
					.activateDefaultTyping(
							BasicPolymorphicTypeValidator.builder()
									// 这相当宽松，只建议用于你自己可控的历史数据兼容
									.allowIfBaseType(Object.class)
									.build(),
							DefaultTyping.NON_FINAL,
							JsonTypeInfo.As.WRAPPER_ARRAY
					)
					.build();
	/**
	 * 对象转json
	 * @param object 对象
	 * @return json
	 */
	public static String toJsonString(Object object) {
		try {
			return OBJECT_MAPPER.writeValueAsString(object);
		}
		catch (JacksonException e) {
			logger.error("toJsonString() error: {}", e.getMessage());
		}
		return "";
	}

	/**
	 * json转换换成对象
	 * @param json json
	 * @param clazz clazz
	 * @return Class
	 */
	public static <T> T parseObject(String json, Class<T> clazz) {
		if (json == null) {
			return null;
		}
		T result = null;
		try {
			result = OBJECT_MAPPER.readValue(json, clazz);
		}
		catch (Exception e) {
			logger.error("parseObject() error: {}", e.getMessage());
		}
		return result;
	}

	/**
	 * json转换换成对象
	 * @param src src
	 * @param clazz clazz
	 * @return Class
	 */
	public static <T> T parseObject(byte[] src, Class<T> clazz) {
		T result = null;
		try {
			result = OBJECT_MAPPER.readValue(src, clazz);
		}
		catch (Exception e) {
			logger.error("parseObject() error: {}", e.getMessage());
		}
		return result;
	}

	public static ObjectMapper getObjectMapper() {
		return OBJECT_MAPPER;
	}

	/**
	 * *
	 * https://stackoverflow.com/questions/6349421/how-to-use-jackson-to-deserialise-an-array-of-objects
	 * * List<MyClass> myObjects = Arrays.asList(mapper.readValue(json, MyClass[].class))
	 * * works up to 10 time faster than TypeRefence.
	 * @return List数组
	 */
	public static <T> List<T> parseArray(String json, Class<T[]> clazz) {
		if (json == null) {
			return null;
		}
		T[] result = null;
		try {
			result = OBJECT_MAPPER.readValue(json, clazz);
		}
		catch (Exception e) {
			logger.error("parseArray() error: {}", e.getMessage());
		}
		if (result == null) {
			return Collections.emptyList();
		}
		return Arrays.asList(result);
	}

	public static <T> List<T> parseArray(byte[] src, Class<T[]> clazz) {
		T[] result = null;
		try {
			result = OBJECT_MAPPER.readValue(src, clazz);
		}
		catch (Exception e) {
			logger.error("parseArray() error: {}", e.getMessage());
		}
		if (result == null) {
			return Collections.emptyList();
		}
		return Arrays.asList(result);
	}


	/**
	 * 转换成json节点，即map
	 * @param jsonStr jsonStr
	 * @return JsonNode
	 */
	public static JsonNode parseJson(String jsonStr) {
		if (jsonStr == null) {
			return null;
		}
		JsonNode jsonNode = null;
		try {
			jsonNode = OBJECT_MAPPER.readTree(jsonStr);
		}
		catch (Exception e) {
			logger.error("parseJson() error: {}", e.getMessage());
		}
		return jsonNode;
	}

	public static ObjectMapper getRedisObjectMapper() {
		return REDIS_OBJECT_MAPPER;
	}
}
