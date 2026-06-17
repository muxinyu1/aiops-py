package top.tangyh.lamp.job.facade;

import top.tangyh.basic.base.R;
import top.tangyh.lamp.job.dto.XxlJobInfoVO;

/**
 * @author zuihou
 * @since 2024年09月21日00:15:26
 */
public interface JobFacade {
    /**
     * 定时发送接口
     *
     * @param xxlJobInfo 任务
     * @return 任务id
     */
    R<String> addTimingTask(XxlJobInfoVO xxlJobInfo);

}
