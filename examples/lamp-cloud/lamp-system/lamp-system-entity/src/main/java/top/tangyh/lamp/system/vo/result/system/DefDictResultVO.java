package top.tangyh.lamp.system.vo.result.system;

import cn.hutool.core.map.MapUtil;
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
import java.util.List;
import java.util.Map;

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
@Schema(description = "字典")
public class DefDictResultVO extends Entity<Long> implements Serializable, EchoVO {
    @Serial
    private static final long serialVersionUID = 1L;
    @Builder.Default
    private final Map<String, Object> echoMap = MapUtil.newHashMap();

    @Schema(description = "ID")
    private Long id;


    /**
     * 分类;[10-系统字典 20-业务字典]@Echo(api = EchoApi.DICTIONARY_ITEM_FEIGN_CLASS, dictType = EchoDictType.System.DICT_CLASSIFY)
     */
    @Schema(description = "分类")
    private String classify;

    /**
     * 字典分组
     */
    @Schema(description = "字典分组")
    private String dictGroup;

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
     * 查询枚举字典时使用
     */
    @Schema(description = "枚举是否存在")
    private Boolean exist;

    private List<DefDictItemResultVO> itemList;
}
