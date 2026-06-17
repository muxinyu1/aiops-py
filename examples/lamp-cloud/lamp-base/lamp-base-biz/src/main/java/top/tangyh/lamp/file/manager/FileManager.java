package top.tangyh.lamp.file.manager;

import top.tangyh.basic.base.manager.SuperManager;
import top.tangyh.lamp.file.entity.File;
import top.tangyh.lamp.file.vo.result.FileResultVO;

import java.util.List;

/**
 * 文件
 *
 * @author zuihou
 * @date 2021/10/31 10:24
 */
public interface FileManager extends SuperManager<File> {
    /**
     * 根据业务id 和 业务类型 查询附件
     * <p>
     * 返回值为： [附件, ...]
     *
     * @param bizId   业务id
     * @param bizType 业务类型
     * @return 附件
     */
    List<FileResultVO> listByBizIdAndBizType(Long bizId, String bizType);

}
