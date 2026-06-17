package top.tangyh.lamp.oauth.facde.impl;

import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import top.tangyh.basic.base.R;
import top.tangyh.lamp.oauth.facade.CaptchaFacade;
import top.tangyh.lamp.oauth.service.CaptchaService;

/**
 *
 * @author tangyh
 * @since 2024/9/20 15:42
 */
@Service
@RequiredArgsConstructor
public class CaptchaFacadeImpl implements CaptchaFacade {

    private final CaptchaService captchaService;

    public Boolean check(String key, String code, String templateCode) {
        R<Boolean> result = captchaService.checkCaptcha(key, code, templateCode);
        return result.getData();
    }
}
