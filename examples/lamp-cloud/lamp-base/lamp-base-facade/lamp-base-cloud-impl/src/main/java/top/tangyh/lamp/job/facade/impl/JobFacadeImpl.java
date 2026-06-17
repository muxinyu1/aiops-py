package top.tangyh.lamp.job.facade.impl;

import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Lazy;
import org.springframework.stereotype.Service;
import top.tangyh.basic.base.R;
import top.tangyh.lamp.job.api.JobApi;
import top.tangyh.lamp.job.dto.XxlJobInfoVO;
import top.tangyh.lamp.job.facade.JobFacade;

/**
 *
 * @author tangyh
 * @since 2024/9/21 00:15
 */
@Service
@RequiredArgsConstructor
public class JobFacadeImpl implements JobFacade {
    @Autowired
    @Lazy  // 一定要延迟加载，否则lamp-gateway-server无法启动
    private JobApi jobApi;

    @Override
    public R<String> addTimingTask(XxlJobInfoVO xxlJobInfo) {
        return jobApi.addTimingTask(xxlJobInfo);
    }
}
