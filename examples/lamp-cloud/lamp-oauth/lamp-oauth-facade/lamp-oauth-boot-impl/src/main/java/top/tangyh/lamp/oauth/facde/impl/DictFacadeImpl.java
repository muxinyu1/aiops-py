package top.tangyh.lamp.oauth.facde.impl;

import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import top.tangyh.lamp.model.constant.EchoApi;
import top.tangyh.lamp.oauth.facade.DictFacade;
import top.tangyh.lamp.oauth.service.DictService;

import java.io.Serializable;
import java.util.Map;
import java.util.Set;

/**
 * 字典实现
 * @author tangyh
 * @since 2024/9/20 23:29
 */
@Service(EchoApi.DICTIONARY_ITEM_FEIGN_CLASS)
@RequiredArgsConstructor
public class DictFacadeImpl implements DictFacade {
    private final DictService dictService;

    @Override
    public Map<Serializable, Object> findByIds(Set<Serializable> ids) {
        return dictService.findByIds(ids);
    }
}
