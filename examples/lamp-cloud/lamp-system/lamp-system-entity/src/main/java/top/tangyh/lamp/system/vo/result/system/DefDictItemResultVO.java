package top.tangyh.lamp.system.vo.result.system;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.EqualsAndHashCode;
import lombok.NoArgsConstructor;
import lombok.ToString;
import lombok.experimental.Accessors;
import top.tangyh.basic.base.entity.Entity;
import top.tangyh.basic.interfaces.echo.EchoVO;

import java.io.Serial;
import java.io.Serializable;
import java.util.HashMap;
import java.util.Map;


/**
 * <p>
 * 实体类
 * 字典项
 * </p>
 *
 * @author zuihou
 * @since 2021-10-04
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
@Accessors(chain = true)
@ToString(callSuper = true)
@EqualsAndHashCode(callSuper = false)
@Builder
@Schema(title = "DefDictItemResultVO", description = "字典项")
public class DefDictItemResultVO extends Entity<Long> implements Serializable, EchoVO {

    @Serial
    private static final long serialVersionUID = 1L;
    @Builder.Default
    private Map<String, Object> echoMap = new HashMap<>();

    @Schema(description = "ID")
    private Long id;

    /**
     * 字典ID
     */
    @Schema(description = "字典ID")
    private Long parentId;
    /**
     * 父字典标识
     */
    @Schema(description = "父字典标识")
    private String parentKey;
    /**
     * 字典分组
     */
    @Schema(description = "字典分组")
    private String dictGroup;
    /**
     * 分类
     * [10-系统字典 20-业务字典]
     * @Echo(api = EchoApi.DICTIONARY_ITEM_FEIGN_CLASS, dictType = EchoDictType.System.DICT_CLASSIFY)
     */
    @Schema(description = "分类")
    private String classify;
    /**
     * 数据类型
     * [1-字符串 2-整型 3-布尔]
     */
    @Schema(description = "数据类型")
    private String dataType;
    /**
     * 标识
     */
    @Schema(description = "标识")
    private String key;
    /**
     * 名称
     */
    @Schema(description = "名称")
    private String name;
    /**
     * 状态
     */
    @Schema(description = "状态")
    private Boolean state;
    /**
     * 备注
     */
    @Schema(description = "备注")
    private String remark;
    /**
     * 排序
     */
    @Schema(description = "排序")
    private Integer sortValue;
    /**
     * 图标
     */
    @Schema(description = "图标")
    private String icon;
    /**
     * css样式
     */
    @Schema(description = "css样式")
    private String cssStyle;
    /**
     * css类元素
     */
    @Schema(description = "css类元素")
    private String cssClass;
    /**
     * 组件属性
     * 用于Tag时，用于配置color属性
     * 用于Button时，用于配置type属性
     */
    @Schema(description = "组件属性")
    private String propType;
    /**
     * 国际化配置
     */
    @Schema(description = "国际化配置")
    private String i18nJson;

    /**
     * 查询枚举字典时使用
     */
    @Schema(description = "枚举是否存在")
    private Boolean exist;

}
