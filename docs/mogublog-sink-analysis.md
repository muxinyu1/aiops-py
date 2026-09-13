# MoGuBlog editSystemConfig Sink 分析

API 入口: `POST /systemConfig/editSystemConfig`
Controller: `SystemConfigRestApi.editSystemConfig(@RequestBody SystemConfigVO)`
Service: `SystemConfigServiceImpl.editSystemConfig(SystemConfigVO)`

## 枚举值域

| 枚举类 | 常量 | 值 |
|--------|------|----|
| EOpenStatus | CLOSE | "0" |
| EOpenStatus | OPEN | "1" |
| EFilePriority | LOCAL | "0" |
| EFilePriority | QI_NIU | "1" |
| EFilePriority | MINIO | "2" |

## Sink 1: 图片必须选择上传到一个区域 (line 99)

```java
if (EOpenStatus.CLOSE.equals(systemConfigVO.getUploadLocal())
    && EOpenStatus.CLOSE.equals(systemConfigVO.getUploadQiNiu())
    && EOpenStatus.CLOSE.equals(systemConfigVO.getUploadMinio())) {
    return ResultUtil.errorWithMessage(MessageConf.PICTURE_MUST_BE_SELECT_AREA);
}
```

- 触发条件: `uploadLocal="0" && uploadQiNiu="0" && uploadMinio="0"`
- 响应消息: `"图片必须选择上传到一个区域"` (固定字符串)
- 可 fuzz: **否**。消息为硬编码常量，无 `{}` 占位符，攻击标记无法注入日志输出。

## Sink 2: 必须开启图片上传本地 (line 103)

```java
if ((EFilePriority.LOCAL.equals(systemConfigVO.getPicturePriority())
        || EFilePriority.LOCAL.equals(systemConfigVO.getContentPicturePriority()))
        && EOpenStatus.CLOSE.equals(systemConfigVO.getUploadLocal())) {
    return ResultUtil.errorWithMessage(MessageConf.MUST_BE_OPEN_LOCAL_UPLOAD);
}
```

- 触发条件: `(picturePriority="0" || contentPicturePriority="0") && uploadLocal="0"`，且需绕过 Sink 1（至少一个 upload 开启）
- 实际最小触发: `uploadLocal="0", uploadQiNiu="1", picturePriority="0"`
- 响应消息: `"图片显示优先级为本地优先，必须开启图片上传本地"` (固定字符串)
- 可 fuzz: **否**。同上，硬编码常量。

## Sink 3: 必须开启七牛云上传 (line 109)

```java
if ((EFilePriority.QI_NIU.equals(systemConfigVO.getPicturePriority())
        || EFilePriority.QI_NIU.equals(systemConfigVO.getContentPicturePriority()))
        && EOpenStatus.CLOSE.equals(systemConfigVO.getUploadQiNiu())) {
    return ResultUtil.errorWithMessage(MessageConf.MUST_BE_OPEN_QI_NIU_UPLOAD);
}
```

- 触发条件: `(picturePriority="1" || contentPicturePriority="1") && uploadQiNiu="0"`，且需绕过 Sink 1-2
- 实际最小触发: `uploadLocal="1", uploadQiNiu="0", picturePriority="1"`
- 响应消息: `"图片显示优先级为七牛云优先，必须开启图片上传七牛云"` (固定字符串)
- 可 fuzz: **否**。同上。

## Sink 4: 必须开启Minio上传 (line 115)

```java
if ((EFilePriority.MINIO.equals(systemConfigVO.getPicturePriority())
        || EFilePriority.MINIO.equals(systemConfigVO.getContentPicturePriority()))
        && EOpenStatus.CLOSE.equals(systemConfigVO.getUploadMinio())) {
    return ResultUtil.errorWithMessage(MessageConf.MUST_BE_OPEN_MINIO_UPLOAD);
}
```

- 触发条件: `(picturePriority="2" || contentPicturePriority="2") && uploadMinio="0"`，且需绕过 Sink 1-3
- 实际最小触发: `uploadLocal="1", uploadMinio="0", picturePriority="2"`
- 响应消息: `"图片显示优先级为Minio对象存储，必须开启图片上传Minio对象存储"` (固定字符串)
- 可 fuzz: **否**。同上。

## Sink 5: 必须设置邮箱 (line 121)

```java
if (EOpenStatus.OPEN.equals(systemConfigVO.getStartEmailNotification())
    && StringUtils.isEmpty(systemConfigVO.getEmail())) {
    return ResultUtil.errorWithMessage(MessageConf.MUST_BE_SET_EMAIL);
}
```

- 触发条件: `startEmailNotification="1" && email=""(空)`，且需绕过 Sink 1-4
- 实际最小触发: `uploadLocal="1", picturePriority="0", startEmailNotification="1", email=""`
- 响应消息: `"开启邮件通知，必须设置邮箱地址"` (固定字符串)
- 可 fuzz: **否**。同上。

## 总结

| Sink | 行号 | 涉及参数数量 | 条件复杂度 | 可 fuzz |
|------|------|-------------|-----------|---------|
| 图片必须选择上传区域 | 99 | 3 | 3 个 AND | 否 |
| 必须开启本地上传 | 103 | 3+ | 2 个 OR + 1 个 AND + 绕过 Sink1 | 否 |
| 必须开启七牛云上传 | 109 | 3+ | 2 个 OR + 1 个 AND + 绕过 Sink1-2 | 否 |
| 必须开启Minio上传 | 115 | 3+ | 2 个 OR + 1 个 AND + 绕过 Sink1-3 | 否 |
| 必须设置邮箱 | 121 | 4+ | 1 个 AND + 绕过 Sink1-4 | 否 |

5 个 sink 全部使用 `ResultUtil.errorWithMessage(MessageConf.XXX)` 返回硬编码的固定错误消息，消息中不包含任何用户可控内容（无 `{}` 占位符、无字符串拼接），因此攻击标记无法通过请求参数注入到日志/响应输出中。

这 5 个 sink 适合作为**多参数组合条件到达性测试**的案例（验证 LLM 能否通过偏差反馈推理出正确的枚举值组合到达 sink），但不适合作为 Log Injection fuzz 目标。
