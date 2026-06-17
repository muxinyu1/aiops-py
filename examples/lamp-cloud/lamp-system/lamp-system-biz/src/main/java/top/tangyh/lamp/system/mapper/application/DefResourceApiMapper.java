package top.tangyh.lamp.system.mapper.application;

import com.baomidou.mybatisplus.annotation.InterceptorIgnore;
import org.apache.ibatis.annotations.Select;
import org.springframework.stereotype.Repository;
import top.tangyh.basic.base.mapper.SuperMapper;
import top.tangyh.lamp.model.vo.result.ResourceApiVO;
import top.tangyh.lamp.system.entity.application.DefResourceApi;

import java.util.List;

/**
 * <p>
 * Mapper 接口
 * 资源接口
 * </p>
 *
 * @author zuihou
 * @date 2021-09-17
 */
@Repository
@InterceptorIgnore(tenantLine = "true", dynamicTableName = "true")
public interface DefResourceApiMapper extends SuperMapper<DefResourceApi> {

    /** 查询系统中配置的所有API与资源编码 */
    @Select("""
            select ra.uri ,ra.request_method , r.code from def_resource_api ra inner join def_resource r on r.id = ra.resource_id 
            where r.state  = 1 order by r.sort_value asc
            """)
    List<ResourceApiVO> findAllApi();
}
