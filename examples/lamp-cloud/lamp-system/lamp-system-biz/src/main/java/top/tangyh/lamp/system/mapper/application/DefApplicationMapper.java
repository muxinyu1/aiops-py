package top.tangyh.lamp.system.mapper.application;

import com.baomidou.mybatisplus.annotation.InterceptorIgnore;
import org.apache.ibatis.annotations.Param;
import org.springframework.stereotype.Repository;
import top.tangyh.basic.base.mapper.SuperMapper;
import top.tangyh.lamp.system.entity.application.DefApplication;
import top.tangyh.lamp.system.vo.result.application.DefApplicationResultVO;

import java.util.List;

/**
 * <p>
 * Mapper 接口
 * 应用
 * </p>
 *
 * @author zuihou
 * @date 2021-09-15
 */
@Repository
@InterceptorIgnore(tenantLine = "true", dynamicTableName = "true")
public interface DefApplicationMapper extends SuperMapper<DefApplication> {

    /**
     * 查询我的应用
     *
     * @param name 应用名
     * @return
     */
    List<DefApplicationResultVO> findMyApplication(@Param("name") String name);

}
