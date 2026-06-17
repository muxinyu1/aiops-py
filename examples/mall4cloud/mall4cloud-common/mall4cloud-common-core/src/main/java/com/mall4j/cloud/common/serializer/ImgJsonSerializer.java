package com.mall4j.cloud.common.serializer;

import cn.hutool.core.util.StrUtil;
import tools.jackson.core.JsonGenerator;
import com.mall4j.cloud.common.util.PrincipalUtil;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.cloud.context.config.annotation.RefreshScope;
import org.springframework.stereotype.Component;
import tools.jackson.databind.SerializationContext;
import tools.jackson.databind.ValueSerializer;

import java.io.IOException;

/**
 * @author FrozenWatermelon
 * @date 2020/09/03
 */
@Component
@RefreshScope
public class ImgJsonSerializer extends ValueSerializer<String> {

	@Value("${biz.oss.resources-url}")
	private String imgDomain;

	@Override
	public void serialize(String value, JsonGenerator gen, SerializationContext serializers) {
		if (StrUtil.isBlank(value)) {
			gen.writeString(StrUtil.EMPTY);
			return;
		} else if (StrUtil.isBlank(imgDomain)) {
			gen.writeString(value);
			return;
		}
		String[] imgs = value.split(StrUtil.COMMA);
		StringBuilder sb = new StringBuilder();

		for (String img : imgs) {
			// 图片为http协议开头，直接返回
			if (PrincipalUtil.isHttpProtocol(img)) {
				sb.append(img).append(StrUtil.COMMA);
			}
			else {
				sb.append(imgDomain).append(img).append(StrUtil.COMMA);
			}
		}
		sb.deleteCharAt(sb.length() - 1);
		gen.writeString(sb.toString());
	}
}
