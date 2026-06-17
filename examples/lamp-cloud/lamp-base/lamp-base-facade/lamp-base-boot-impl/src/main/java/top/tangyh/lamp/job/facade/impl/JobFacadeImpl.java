package top.tangyh.lamp.job.facade.impl;

import cn.hutool.http.HttpRequest;
import cn.hutool.json.JSONUtil;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import top.tangyh.basic.base.R;
import top.tangyh.lamp.job.dto.XxlJobInfoVO;
import top.tangyh.lamp.job.facade.JobFacade;

/**
 *
 * @author tangyh
 * @since 2024/9/21 00:09
 */
@Service
@RequiredArgsConstructor
public class JobFacadeImpl implements JobFacade {
    @Value("${lamp.feign.job-server:http://127.0.0.1:8767}")
    private String jobServerUrl;

    @Override
    public R<String> addTimingTask(XxlJobInfoVO xxlJobInfo) {
        String url = "/xxl-job-admin/jobinfo/save";
        String result = HttpRequest.post(jobServerUrl + url)
                .body(JSONUtil.toJsonStr(xxlJobInfo))
                .timeout(20000)//超时，毫秒
                .execute().body();
        return R.success(result);
    }


}
