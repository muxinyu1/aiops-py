package top.tangyh.lamp.oauth.facde.impl;

import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import top.tangyh.basic.model.log.OptLogDTO;
import top.tangyh.basic.utils.BeanPlusUtil;
import top.tangyh.lamp.base.service.system.BaseOperationLogService;
import top.tangyh.lamp.base.vo.save.system.BaseOperationLogSaveVO;
import top.tangyh.lamp.oauth.facade.LogFacade;

/**
 * 操作日志保存 API
 *
 * @author zuihou
 * @date 2019/07/02
 */
@Service
@RequiredArgsConstructor
public class LogFacadeImpl implements LogFacade {
    private final BaseOperationLogService baseOperationLogService;

    /**
     * 保存日志
     *
     * @param data 操作日志
     * @return 操作日志
     */
    public void save(OptLogDTO data) {
        BaseOperationLogSaveVO bean = BeanPlusUtil.toBean(data, BaseOperationLogSaveVO.class);
        baseOperationLogService.save(bean);
    }

}
