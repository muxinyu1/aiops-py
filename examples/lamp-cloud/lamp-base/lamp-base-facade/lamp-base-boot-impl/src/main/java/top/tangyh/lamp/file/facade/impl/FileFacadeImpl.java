package top.tangyh.lamp.file.facade.impl;


import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;
import top.tangyh.lamp.file.enumeration.FileStorageType;
import top.tangyh.lamp.file.facade.FileFacade;
import top.tangyh.lamp.file.service.FileService;
import top.tangyh.lamp.file.vo.param.FileUploadVO;
import top.tangyh.lamp.file.vo.result.FileResultVO;

/**
 * 文件接口
 *
 * @author zuihou
 * @since 2024年09月20日10:45:54
 */
@Service
@RequiredArgsConstructor
public class FileFacadeImpl implements FileFacade {
    private final FileService fileService;


    @Override
    public FileResultVO upload(MultipartFile file, String bizType, String bucket, FileStorageType storageType) {
        FileUploadVO fileUploadVO = new FileUploadVO();
        fileUploadVO.setBizType(bizType);
        fileUploadVO.setBucket(bucket);
        fileUploadVO.setStorageType(storageType);
        return fileService.upload(file, fileUploadVO);
    }
}
