package top.tangyh.lamp.msg.facade.impl;


import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Lazy;
import org.springframework.stereotype.Service;
import top.tangyh.basic.base.R;
import top.tangyh.lamp.msg.api.MsgApi;
import top.tangyh.lamp.msg.facade.MsgFacade;
import top.tangyh.lamp.msg.vo.update.ExtendMsgSendVO;

/**
 * 消息接口
 *
 * @author zuihou
 * @since 2024年09月20日10:37:50
 *
 */
@Service
public class MsgFacadeImpl implements MsgFacade {
    @Lazy
    @Autowired
    private MsgApi msgApi;

    /**
     * 根据模板发送消息
     *
     * @param data 发送内容
     * @return
     */
    @Override
    public Boolean sendByTemplate(ExtendMsgSendVO data) {
        R<Boolean> result = msgApi.sendByTemplate(data);
        return result.getIsSuccess() && result.getData();
    }
}
