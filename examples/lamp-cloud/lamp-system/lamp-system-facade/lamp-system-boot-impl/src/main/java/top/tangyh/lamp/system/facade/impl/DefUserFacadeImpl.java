package top.tangyh.lamp.system.facade.impl;

import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import top.tangyh.basic.base.R;
import top.tangyh.lamp.model.constant.EchoApi;
import top.tangyh.lamp.system.facade.DefUserFacade;
import top.tangyh.lamp.system.service.tenant.DefUserService;

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
    private final DefUserService defUserService;

    @Override
    public R<List<Long>> findAllUserId() {
        return R.success(defUserService.findUserIdList(null));
    }

    @Override
    public Map<Serializable, Object> findByIds(Set<Serializable> ids) {
        return defUserService.findByIds(ids);
    }

}
