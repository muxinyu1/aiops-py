package top.tangyh.lamp.system.entity.system;

import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.EqualsAndHashCode;
import lombok.NoArgsConstructor;
import lombok.ToString;
import lombok.experimental.Accessors;
import top.tangyh.basic.base.entity.Entity;

import java.io.Serial;
import java.time.LocalDateTime;

import static com.baomidou.mybatisplus.annotation.SqlCondition.EQUAL;
import static top.tangyh.lamp.model.constant.Condition.LIKE;


/**
 * <p>
 * 实体类
 * 字典
 * </p>
 *
 * @author zuihou
 * @since 2021-10-04
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
@ToString(callSuper = true)
@Accessors(chain = true)
@EqualsAndHashCode(callSuper = true)
@Builder
@TableName("def_dict")
public class DefDict extends Entity<Long> {
    @Serial
    private static final long serialVersionUID = 1L;

    /**
     * 字典ID
     */
    @TableField(value = "parent_id", condition = EQUAL)
    private Long parentId;
    /**
     * 父字典标识
     */
    @TableField(value = "parent_key", condition = LIKE)
    private String parentKey;
    /**
     * 字典分组
     */
    @TableField(value = "dict_group", condition = LIKE)
    private String dictGroup;
    /**
     * 分类
     * [10-系统字典 20-业务字典]
     * @Echo(api = EchoApi.DICTIONARY_ITEM_FEIGN_CLASS, dictType = EchoDictType.System.DICT_CLASSIFY)
     */
    @TableField(value = "classify", condition = LIKE)
    private String classify;
    /**
     * 数据类型
     * [1-字符串 2-整型 3-布尔]
     */
    @TableField(value = "data_type", condition = LIKE)
    private String dataType;
    /**
     * 标识
     */
    @TableField(value = "key_", condition = LIKE)
    private String key;
    /**
     * 名称
     */
    @TableField(value = "name", condition = LIKE)
    private String name;
    /**
     * 状态
     */
    @TableField(value = "state", condition = EQUAL)
    private Boolean state;
    /**
     * 备注
     */
    @TableField(value = "remark", condition = LIKE)
    private String remark;
    /**
     * 排序
     */
    @TableField(value = "sort_value", condition = EQUAL)
    private Integer sortValue;
    /**
     * 图标
     */
    @TableField(value = "icon", condition = LIKE)
    private String icon;
    /**
     * css样式
     */
    @TableField(value = "css_style", condition = LIKE)
    private String cssStyle;
    /**
     * css类元素
     */
    @TableField(value = "css_class", condition = LIKE)
    private String cssClass;
    /**
     * 组件属性
     * 用于Tag时，用于配置color属性
     * 用于Button时，用于配置type属性
     */
    @TableField(value = "prop_type", condition = LIKE)
    private String propType;
    /**
     * 国际化配置
     */
    @TableField(value = "i18n_json", condition = LIKE)
    private String i18nJson;


    @Builder
    public DefDict(Long id, Long createdBy, LocalDateTime createdTime, Long updatedBy, LocalDateTime updatedTime,
                   Long parentId, String parentKey, String classify, String key, String name,
                   Boolean state, String remark, Integer sortValue, String icon, String cssStyle, String cssClass,
                   String dictGroup, String dataType, String propType, String i18nJson) {
        this.id = id;
        this.createdBy = createdBy;
        this.createdTime = createdTime;
        this.updatedBy = updatedBy;
        this.updatedTime = updatedTime;
        this.parentId = parentId;
        this.parentKey = parentKey;
        this.classify = classify;
        this.key = key;
        this.name = name;
        this.state = state;
        this.remark = remark;
        this.sortValue = sortValue;
        this.icon = icon;
        this.cssStyle = cssStyle;
        this.cssClass = cssClass;
        this.dictGroup = dictGroup;
        this.dataType = dataType;
        this.propType = propType;
        this.i18nJson = i18nJson;
    }

}
