package top.tangyh.lamp.oauth.api;

import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import top.tangyh.basic.base.R;
import top.tangyh.basic.constant.Constants;

/**
 * 验证码
 * @author tangyh
 * @version v1.0
 * @since 2024年09月20日15:41:02
 */
@FeignClient(name = "${" + Constants.PROJECT_PREFIX + ".feign.oauth-server:lamp-oauth-server}", path = "/anyTenant")
public interface CaptchaApi {
    /***
     * 检查验证码是否正确
     * @param key 唯一键
     * @param code 验证码
     * @param templateCode 模版
     * @return
     */
    @GetMapping(value = "/anyTenant/checkCaptcha")
    R<Boolean> check(@RequestParam(value = "key") String key, @RequestParam(value = "code") String code, @RequestParam(value = "templateCode") String templateCode);
}
