package top.tangyh.lamp.common.config;

import org.springframework.boot.autoconfigure.condition.ConditionalOnExpression;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import top.tangyh.basic.boot.config.BaseConfig;
import top.tangyh.basic.constant.Constants;
import top.tangyh.basic.log.event.SysLogListener;
import top.tangyh.lamp.oauth.facade.LogFacade;

/**
 * 基础服务-Web配置
 *
 * @author zuihou
 * @date 2021-10-08
 */
@Configuration
public class WebConfiguration extends BaseConfig {

    /**
     * lamp.log.enabled = true 并且 lamp.log.type=DB时实例该类
     */
    @Bean
    @ConditionalOnExpression("${" + Constants.PROJECT_PREFIX + ".log.enabled:true} && 'DB'.equals('${" + Constants.PROJECT_PREFIX + ".log.type:LOGGER}')")
    public SysLogListener sysLogListener(LogFacade logApi) {
        return new SysLogListener(logApi::save);
    }
}
