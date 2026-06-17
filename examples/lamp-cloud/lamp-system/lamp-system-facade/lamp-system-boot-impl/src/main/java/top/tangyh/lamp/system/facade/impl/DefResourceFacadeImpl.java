package top.tangyh.lamp.system.facade.impl;

import cn.hutool.core.util.StrUtil;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import top.tangyh.basic.cache.redis2.CacheResult;
import top.tangyh.basic.cache.repository.CacheOps;
import top.tangyh.basic.model.cache.CacheKey;
import top.tangyh.lamp.common.cache.tenant.application.AllResourceApiCacheKeyBuilder;
import top.tangyh.lamp.model.vo.result.ResourceApiVO;
import top.tangyh.lamp.system.facade.DefResourceFacade;
import top.tangyh.lamp.system.service.application.DefResourceService;

import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

/**
 *
 * @author tangyh
 * @since 2024/9/21 22:21
 */
@Service
@RequiredArgsConstructor
public class DefResourceFacadeImpl implements DefResourceFacade {
    private final DefResourceService defResourceService;
    private final CacheOps cacheOps;

    @Override
    public Map<String, Set<String>> listAllApi() {
        CacheKey cacheKey = AllResourceApiCacheKeyBuilder.builder();
        CacheResult<Map<String, Set<String>>> result = cacheOps.get(cacheKey, (k) -> findAllApi());
        return result.getValue();
    }

    private Map<String, Set<String>> findAllApi() {
        // 查询系统中配置的URI和权限关系
        List<ResourceApiVO> list = defResourceService.findAllApi();
        return list.stream()
                // 这里单体版和微服务版不同
                .peek(item -> {
                    String uri = item.getUri();
                    if (!StrUtil.startWithAny(uri, "/gateway")) {
                        uri = StrUtil.subSuf(uri, StrUtil.indexOf(uri, '/', 1));
                    }
                    item.setUri(uri);
                })
                .collect(Collectors.toMap(
                        item -> item.getUri() + "###" + item.getRequestMethod(),
                        resourceApiVO -> {
                            Set<String> codes = new HashSet<>();
                            codes.add(resourceApiVO.getCode());
                            return codes;
                        },
                        (existingCodes, newCodes) -> {
                            existingCodes.addAll(newCodes);
                            return existingCodes;
                        },
                        LinkedHashMap::new
                ));
    }
}
