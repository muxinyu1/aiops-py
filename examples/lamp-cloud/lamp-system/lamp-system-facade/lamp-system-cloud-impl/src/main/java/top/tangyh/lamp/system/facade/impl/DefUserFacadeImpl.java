package top.tangyh.lamp.system.facade.impl;

import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Lazy;
import org.springframework.stereotype.Service;
import top.tangyh.basic.base.R;
import top.tangyh.lamp.model.constant.EchoApi;
import top.tangyh.lamp.system.api.DefUserApi;
import top.tangyh.lamp.system.facade.DefUserFacade;

import java.io.Serializable;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 *
 * @author tangyh
 * @since 2024/9/20 23:33
 */
@Service(EchoApi.DEF_USER_ID_CLASS)
@RequiredArgsConstructor
public class DefUserFacadeImpl implements DefUserFacade {
    @Autowired
    @Lazy  // 一定要延迟加载，否则lamp-gateway-server无法启动
    private DefUserApi defUserApi;

    @Override
    public R<List<Long>> findAllUserId() {
        return defUserApi.findAllUserId();
    }

    @Override
    public Map<Serializable, Object> findByIds(Set<Serializable> ids) {
        return defUserApi.findByIds(ids);
    }

}

