package top.tangyh.lamp.model.vo.result;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.EqualsAndHashCode;
import lombok.NoArgsConstructor;
import lombok.ToString;
import lombok.experimental.Accessors;
import top.tangyh.basic.interfaces.BaseEnum;

import java.util.Arrays;
import java.util.List;

/**
 * @author tangyh
 * @version v1.0
 * @date 2021/4/28 12:15 上午
 * @create [2021/4/28 12:15 上午 ] [tangyh] [初始创建]
 */
@Data
@NoArgsConstructor
@ToString(callSuper = true)
@EqualsAndHashCode(of = "value")
@Accessors(chain = true)
@AllArgsConstructor
@Builder
@Schema(title = "Option", description = "下拉、多选组件选项")
public class Option {
    private String label;
    private String value;
    private String color;
    private String remark;


    public static List<Option> mapOptions(BaseEnum[] values) {
        return Arrays.stream(values).map(item -> Option.builder().label(item.getDesc()).remark(item.getDesc())
                .value(item.getCode()).color(item.getExtra()).build()).toList();
    }


}
