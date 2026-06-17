package top.tangyh.lamp.oauth.facade;

import top.tangyh.basic.model.log.OptLogDTO;

/**
 * 操作日志保存 API
 *
 * @author zuihou
 * @date 2019/07/02
 */
public interface LogFacade {

    /**
     * 保存日志
     *
     * @param log 操作日志
     */
    void save(OptLogDTO log);

}
