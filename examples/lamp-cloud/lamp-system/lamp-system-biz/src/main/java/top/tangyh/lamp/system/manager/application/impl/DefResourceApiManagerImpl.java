package top.tangyh.lamp.system.manager.application.impl;

import cn.hutool.core.convert.Convert;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import top.tangyh.basic.base.manager.impl.SuperCacheManagerImpl;
import top.tangyh.basic.database.mybatis.conditions.Wraps;
import top.tangyh.basic.database.mybatis.conditions.query.LbQueryWrap;
import top.tangyh.basic.model.cache.CacheKey;
import top.tangyh.basic.model.cache.CacheKeyBuilder;
import top.tangyh.lamp.common.cache.tenant.application.ResourceApiCacheKeyBuilder;
import top.tangyh.lamp.common.cache.tenant.application.ResourceResourceApiCacheKeyBuilder;
import top.tangyh.lamp.model.vo.result.ResourceApiVO;
import top.tangyh.lamp.system.entity.application.DefResourceApi;
import top.tangyh.lamp.system.manager.application.DefResourceApiManager;
import top.tangyh.lamp.system.mapper.application.DefResourceApiMapper;

import java.util.List;

/**
 * 应用管理
 *
 * @author tangyh
 * @version v1.0
 * @date 2021/9/29 1:26 下午
 * @create [2021/9/29 1:26 下午 ] [tangyh] [初始创建]
 */
@RequiredArgsConstructor
@Service
@Slf4j
public class DefResourceApiManagerImpl extends SuperCacheManagerImpl<DefResourceApiMapper, DefResourceApi> implements DefResourceApiManager {
    @Override
    protected CacheKeyBuilder cacheKeyBuilder() {
        return new ResourceApiCacheKeyBuilder();
    }

    @Override
    public List<ResourceApiVO> findAllApi() {
        return baseMapper.findAllApi();
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void removeByResourceId(List<Long> resourceIdList) {
        LbQueryWrap<DefResourceApi> wrap = Wraps.<DefResourceApi>lbQ().select(DefResourceApi::getId).in(DefResourceApi::getResourceId, resourceIdList);
        List<Long> apiIds = listObjs(wrap, Convert::toLong);
        remove(wrap);

        CacheKey[] keys = apiIds.stream().map(ResourceApiCacheKeyBuilder::builder).toArray(CacheKey[]::new);
        cacheOps.del(keys);

        CacheKey[] resourceResourceApiKeys = resourceIdList.stream().map(ResourceResourceApiCacheKeyBuilder::builder).toArray(CacheKey[]::new);
        cacheOps.del(resourceResourceApiKeys);
    }

    @Override
    public List<DefResourceApi> findByResourceId(Long resourceId) {
        return list(Wraps.<DefResourceApi>lbQ().eq(DefResourceApi::getResourceId, resourceId));
    }


}
