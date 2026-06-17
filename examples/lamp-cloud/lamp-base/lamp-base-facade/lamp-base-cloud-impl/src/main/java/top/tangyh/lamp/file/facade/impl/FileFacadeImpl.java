package top.tangyh.lamp.file.facade.impl;


import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Lazy;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;
import top.tangyh.basic.base.R;
import top.tangyh.lamp.file.api.FileApi;
import top.tangyh.lamp.file.enumeration.FileStorageType;
import top.tangyh.lamp.file.facade.FileFacade;
import top.tangyh.lamp.file.vo.result.FileResultVO;

/**
 * 文件接口
 *
 * @author zuihou
 * @since 2024年09月20日10:45:54
 */
@Service
public class FileFacadeImpl implements FileFacade {
    @Autowired
    @Lazy
    private FileApi fileApi;

    @Override
    public FileResultVO upload(MultipartFile file, String bizType, String bucket, FileStorageType storageType) {
        R<FileResultVO> result = fileApi.upload(file, bizType, bucket, storageType);
        return result.getData();
    }
}
