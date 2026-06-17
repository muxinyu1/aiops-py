package top.tangyh.lamp.oauth.service;

import top.tangyh.basic.interfaces.echo.LoadService;
import top.tangyh.lamp.system.vo.result.system.DefDictItemResultVO;
import top.tangyh.lamp.system.vo.result.system.DefDictResultVO;

import java.util.List;
import java.util.Map;

/**
 * 字典查询服务
 *
 * @author zuihou
 * @date 2021/10/7 13:27
 */
public interface DictService extends LoadService {
    List<DefDictResultVO> findAll();

    Map<String, List<DefDictItemResultVO>> findDictItemByType(List<String> query);

    void syncEnumToDict();
}
