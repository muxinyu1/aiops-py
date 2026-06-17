package top.tangyh.lamp.msg.controller;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.Parameter;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import top.tangyh.basic.annotation.log.WebLog;
import top.tangyh.basic.annotation.user.LoginUser;
import top.tangyh.basic.base.R;
import top.tangyh.basic.base.entity.SuperEntity;
import top.tangyh.lamp.model.entity.system.SysUser;
import top.tangyh.lamp.msg.biz.MsgBiz;
import top.tangyh.lamp.msg.vo.update.ExtendMsgSendVO;

/**
 * <p>
 * 前端控制器
 * 消息
 * </p>
 *
 * @author zuihou
 * @date 2022-07-10 11:41:17
 * @create [2022-07-10 11:41:17] [zuihou] [代码生成器生成]
 */
@Slf4j
@RequiredArgsConstructor
@Validated
@RestController
@RequestMapping("/anyUser")
@Tag(name = "消息模版")
public class MsgController {
    private final MsgBiz msgBiz;


    @Operation(summary = "根据模板发送消息", description = "根据模板发送消息")
    @PostMapping("/extendMsg/sendByTemplate")
    @WebLog("发送消息")
    public R<Boolean> sendByTemplate(@RequestBody @Validated(SuperEntity.Update.class) ExtendMsgSendVO data, @Parameter(hidden = true) @LoginUser(isEmployee = true) SysUser sysUser) {
        return R.success(msgBiz.sendByTemplate(data, sysUser));
    }

}


