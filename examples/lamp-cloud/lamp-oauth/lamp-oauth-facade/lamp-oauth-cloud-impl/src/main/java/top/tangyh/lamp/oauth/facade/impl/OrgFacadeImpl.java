package top.tangyh.lamp.oauth.facade.impl;

import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Lazy;
import org.springframework.stereotype.Service;
import top.tangyh.lamp.model.constant.EchoApi;
import top.tangyh.lamp.oauth.api.OrgApi;
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
    @Autowired
    @Lazy  // 一定要延迟加载，否则lamp-gateway-server无法启动
    private OrgApi orgApi;

    @Override
    public Map<Serializable, Object> findByIds(Set<Serializable> ids) {
        return orgApi.findByIds(ids);
    }
}
