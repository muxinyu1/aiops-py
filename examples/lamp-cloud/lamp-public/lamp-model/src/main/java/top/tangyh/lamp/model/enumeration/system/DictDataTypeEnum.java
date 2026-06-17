package top.tangyh.lamp.model.enumeration.system;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Getter;
import top.tangyh.basic.interfaces.BaseEnum;

import java.util.stream.Stream;

/**
 * 字典数据类型
 * 必须和数据字典【EchoDictType.System.DATA_TYPE】 保持一致
 *
 * @author tangyh
 * @date 2021/3/15 3:34 下午
 */
@Getter
@Schema(title = "DictDataTypeEnum", description = "字典数据类型-枚举")
public enum DictDataTypeEnum implements BaseEnum {

    /**
     * 字符串
     */
    STRING("1", "字符串"),

    /**
     * 整型
     */
    NUMBER("2", "整型"),
    /**
     * 布尔
     */
    BOOLEAN("3", "布尔");

    private final String code;
    private final String desc;

    DictDataTypeEnum(String code, String desc) {
        this.code = code;
        this.desc = desc;
    }

    /**
     * 根据当前枚举的name匹配
     */
    public static DictDataTypeEnum match(String val, DictDataTypeEnum def) {
        return Stream.of(values()).parallel().filter(item -> item.name().equalsIgnoreCase(val)).findAny().orElse(def);
    }

    public static DictDataTypeEnum get(String val) {
        return match(val, null);
    }

    public boolean eq(DictDataTypeEnum val) {
        return val != null && eq(val.name());
    }

}
