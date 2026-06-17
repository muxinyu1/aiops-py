package top.tangyh.lamp.oauth.facade.impl;

import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Lazy;
import org.springframework.stereotype.Service;
import top.tangyh.basic.base.R;
import top.tangyh.lamp.oauth.api.CaptchaApi;
import top.tangyh.lamp.oauth.facade.CaptchaFacade;

/**
 *
 * @author tangyh
 * @since 2024/9/20 15:42
 */
@Service
@RequiredArgsConstructor
public class CaptchaFacadeImpl implements CaptchaFacade {
    @Autowired
    @Lazy  // 一定要延迟加载，否则lamp-gateway-server无法启动
    private CaptchaApi captchaApi;

    public Boolean check(String key, String code, String templateCode) {
        R<Boolean> check = captchaApi.check(key, code, templateCode);
        return check.getData();
    }
}
