package top.tangyh.lamp.system.vo.query.system;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.EqualsAndHashCode;
import lombok.NoArgsConstructor;
import lombok.ToString;
import lombok.experimental.Accessors;

import java.io.Serial;
import java.io.Serializable;
import java.util.List;


/**
 * <p>
 * 表单查询条件VO
 * 字典
 * </p>
 *
 * @author zuihou
 * @date 2025-09-29 09:59:59
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
@ToString(callSuper = true)
@Accessors(chain = true)
@EqualsAndHashCode
@Builder
@Schema(description = "字典")
public class DefDictPageQuery implements Serializable {

    @Serial
    private static final long serialVersionUID = 1L;
    /**
     * 分类
     * [10-系统字典 20-业务字典]
     * @Echo(api = EchoApi.DICTIONARY_ITEM_FEIGN_CLASS, dictType = EchoDictType.System.DICT_CLASSIFY)
     */
    @Schema(description = "分类")
    private List<String> classify;
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
    private List<Boolean> state;
    /**
     * 备注
     */
    @Schema(description = "备注")
    private String remark;


}
