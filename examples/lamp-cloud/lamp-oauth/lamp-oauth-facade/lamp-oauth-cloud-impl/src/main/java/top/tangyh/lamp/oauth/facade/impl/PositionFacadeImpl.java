package top.tangyh.lamp.oauth.facade.impl;

import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Lazy;
import org.springframework.stereotype.Service;
import top.tangyh.lamp.model.constant.EchoApi;
import top.tangyh.lamp.oauth.api.PositionApi;
import top.tangyh.lamp.oauth.facade.PositionFacade;

import java.io.Serializable;
import java.util.Map;
import java.util.Set;

/**
 * 实现
 * @author tangyh
 * @since 2024/9/20 23:29
 */
@Service(EchoApi.POSITION_ID_CLASS)
@RequiredArgsConstructor
public class PositionFacadeImpl implements PositionFacade {
    @Autowired
    @Lazy  // 一定要延迟加载，否则lamp-gateway-server无法启动
    private PositionApi positionApi;

    @Override
    public Map<Serializable, Object> findByIds(Set<Serializable> ids) {
        return positionApi.findByIds(ids);
    }
}
