package top.tangyh.lamp.oauth.facde.impl;

import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import top.tangyh.lamp.base.service.user.BaseOrgService;
import top.tangyh.lamp.model.constant.EchoApi;
import top.tangyh.lamp.oauth.facade.OrgFacade;

import java.io.Serializable;
import java.util.Map;
import java.util.Set;

/**
 * 实现
 * @author tangyh
 * @since 2024/9/20 23:29
 */
@Service(EchoApi.ORG_ID_CLASS)
@RequiredArgsConstructor
public class OrgFacadeImpl implements OrgFacade {
    private final BaseOrgService baseOrgService;

    @Override
    public Map<Serializable, Object> findByIds(Set<Serializable> ids) {
        return baseOrgService.findByIds(ids);
    }
}
