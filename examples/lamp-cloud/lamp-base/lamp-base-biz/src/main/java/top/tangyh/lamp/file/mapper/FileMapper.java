package top.tangyh.lamp.file.mapper;

import io.lettuce.core.dynamic.annotation.Param;
import org.apache.ibatis.annotations.Select;
import org.springframework.stereotype.Repository;
import top.tangyh.basic.base.mapper.SuperMapper;
import top.tangyh.lamp.file.entity.File;
import top.tangyh.lamp.file.vo.result.FileResultVO;

import java.util.List;

/**
 * <p>
 * Mapper 接口
 * 增量文件上传日志
 * </p>
 *
 * @author tangyh
 * @date 2021-06-30
 * @create [2021-06-30] [tangyh] [初始创建]
 */
@Repository
public interface FileMapper extends SuperMapper<File> {
    /**
     * 查询附件信息
     * @param bizId 业务id
     * @param bizType 业务类型
     * @return
     */
    @Select("select f.* from com_file f left join com_appendix a on f.id = a.id where a.biz_id = #{bizId} and a.biz_type = #{bizType}")
    List<FileResultVO> listByBizIdAndBizType(@Param("bizId") Long bizId, @Param("bizType") String bizType);
}
