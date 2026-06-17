package top.tangyh.lamp.oauth.controller;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;
import top.tangyh.basic.base.R;
import top.tangyh.lamp.oauth.service.DictService;
import top.tangyh.lamp.oauth.service.ParamService;
import top.tangyh.lamp.system.vo.result.system.DefDictItemResultVO;
import top.tangyh.lamp.system.vo.result.system.DefDictResultVO;

import java.util.List;
import java.util.Map;

/**
 * 通用 控制器
 *
 * @author zuihou
 * @date 2019/07/25
 */
@Slf4j
@RestController
@Tag(name = "字典-枚举-参数-通用查询")
@RequiredArgsConstructor
public class GeneralController {


    private final DictService dictService;
    private final ParamService paramService;


    @Operation(summary = "同步枚举到字典", description = "同步枚举到字典")
    @PostMapping("/anyTenant/enums/syncEnumToDict")
    public R<Boolean> syncEnumToDict() {
        dictService.syncEnumToDict();
        return R.success();
    }

    @Operation(summary = "根据字典类型编码批量查询字典项", description = "根据字典类型编码批量查询字典项")
    @PostMapping("/anyUser/dict/findDictItemByType")
    public R<Map<String, List<DefDictItemResultVO>>> findDictItemByType(@RequestBody List<String> query) {
        return R.success(dictService.findDictItemByType(query));
    }

    @PostMapping("/anyUser/parameter/findParamMapByKey")
    @Operation(summary = "根据key批量获取系统参数", description = "根据key批量获取系统参数")
    public R<Map<String, String>> findParams(@RequestBody List<String> keys) {
        return R.success(paramService.findParamMapByKey(keys));
    }


    @Operation(summary = "返回服务中所有枚举类", description = "只能扫描实现了BaseEnum类的枚举")
    @PostMapping("/anyUser/dict/enums/findAll")
    public R<List<DefDictResultVO>> findAll() {
        return R.success(dictService.findAll());
    }
}

