package top.tangyh.lamp.system.vo.update.system;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.EqualsAndHashCode;
import lombok.NoArgsConstructor;
import lombok.ToString;
import lombok.experimental.Accessors;
import top.tangyh.basic.base.entity.SuperEntity;
import top.tangyh.lamp.system.vo.save.system.DefDictItemSaveVO;

import java.io.Serial;
import java.io.Serializable;
import java.util.List;

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
@EqualsAndHashCode
@Builder
@Schema(title = "DefDictUpdateVO", description = "字典")
public class DefDictUpdateVO implements Serializable {
    @Serial
    private static final long serialVersionUID = 1L;

    @Schema(description = "ID")
    @NotNull(message = "请填写ID", groups = SuperEntity.Update.class)
    private Long id;
    /**
     * 字典分组
     */
    @Schema(description = "字典分组")
    @Size(max = 255, message = "字典分组长度不能超过{max}")
    private String dictGroup;
    /**
     * 数据类型
     * [1-字符串 2-整型 3-布尔]
     */
    @Schema(description = "数据类型")
    @Size(max = 1, message = "数据类型长度不能超过{max}")
    private String dataType;
    /**
     * 标识
     */
    @Schema(description = "标识")
    @NotEmpty(message = "请填写标识")
    @Size(max = 255, message = "标识长度不能超过{max}")
    private String key;
    /**
     * 名称
     */
    @Schema(description = "名称")
    @NotEmpty(message = "请填写名称")
    @Size(max = 255, message = "名称长度不能超过{max}")
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
    @Size(max = 255, message = "备注长度不能超过{max}")
    private String remark;

    private List<DefDictItemSaveVO> insertList;
    private List<DefDictItemUpdateVO> updateList;
    private List<Long> deleteList;
}
