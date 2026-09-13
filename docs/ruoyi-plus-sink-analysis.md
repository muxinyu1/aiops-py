# RuoYi-Cloud-Plus SysMenu checkRouteConfigUnique Sink 分析

API 入口: `POST /menu` (新增菜单)
Controller: `SysMenuController.add(@Validated @RequestBody SysMenuBo menu)`
Service: `SysMenuServiceImpl.checkRouteConfigUnique(SysMenuBo)`

认证: Sa-Token, `Authorization: Bearer <access_token>`
登录: `POST /login` body: `{"clientId":"e5cd7e4891bf95d1d19206ce24a7b32e","grantType":"password","tenantId":"000000","username":"admin","password":"admin123"}`
服务端口: auth=8080, system=9201

## 常量值域

| 常量 | 值 | 含义 |
|------|----|------|
| SystemConstants.TYPE_DIR | "M" | 目录 |
| SystemConstants.TYPE_MENU | "C" | 菜单 |
| SystemConstants.TYPE_BUTTON | "F" | 按钮 |
| Constants.TOP_PARENT_ID | 0L | 根目录 |

## 前置条件

```java
if (SystemConstants.TYPE_BUTTON.equals(menu.getMenuType())) {
    return true;  // 按钮类型直接跳过，不检查路由冲突
}
```

所有 sink 的共同前置: `menuType ≠ "F"` (不能是按钮)。

DB 查询: 从 `sys_menu` 表中查出 `menuType` 为 M 或 C、且 `path` 匹配输入 `path` 或 `routeName` 的记录。

## Sink 1: 同级路由冲突 (line 386)

```java
if (StringUtils.equalsAnyIgnoreCase(path, dbPath) && parentId.equals(dbParentId)) {
    log.warn("[同级路由冲突] 同级下已存在相同路由路径 '{}'，冲突菜单：{}", dbPath, sysMenu.getMenuName());
    return false;
}
```

- 触发条件: `menuType ≠ "F"` + `path` 与 DB 中已有菜单的 `path` 相同(不区分大小写) + `parentId` 与已有菜单的 `parentId` 相同
- `{}` 占位符: `dbPath` (DB 中已有菜单的 path) + `menuName` (DB 中已有菜单名)
- 注入方式: 预先创建 path=`sink_attacked` 的种子菜单 (parentId=N)，再发送 path=`sink_attacked` + parentId=N 的冲突请求
- 可 fuzz: **是**。`dbPath` 通过 `{}` 打印到日志，种子菜单的 path 包含攻击标记即可注入。
- Fuzz 结果: **第 2 轮命中** (LLM 第 1 轮盲猜失败，收到偏差反馈+源码后推理出正确参数)

## Sink 2: 根目录路由冲突 (line 391)

```java
} else if (StringUtils.equalsAnyIgnoreCase(path, dbPath)
    && Constants.TOP_PARENT_ID.equals(parentId)
    && Constants.TOP_PARENT_ID.equals(dbParentId)) {
    log.warn("[根目录路由冲突] 根目录下路由 '{}' 必须唯一，已被菜单 '{}' 占用", path, sysMenu.getMenuName());
    return false;
}
```

- 触发条件: `menuType ≠ "F"` + `path` 与 DB 中已有菜单的 `path` 相同 + `parentId == 0` + DB 中菜单的 `parentId == 0`，且需绕过 Sink 1 (实际上 parentId 都是 0 时 Sink 1 会先命中，因此这个分支在代码上仅当 Sink 1 的 parentId 不匹配时才进入 — 但如果 DB 菜单也在根目录则 Sink 1 先拦截)
- `{}` 占位符: `path` (用户直接输入) + `menuName` (DB 中菜单名)
- 注入方式: `path` 字段直接设为 `sink_attacked`，`parentId=0`，`menuType="M"` 或 `"C"`
- 可 fuzz: **是**。`path` 是用户直接输入，通过 `{}` 打印到日志。
- Fuzz 结果: **第 2 轮命中**

## Sink 3: 路由名称冲突 (line 395)

```java
} else if (StringUtils.equalsAnyIgnoreCase(routeName, dbRouteName)
    && sysMenu.getMenuType().equals(menu.getMenuType())) {
    log.warn("[路由名称冲突] 路由名称 '{}' 需全局唯一，已被菜单 '{}' 使用", routeName, sysMenu.getMenuName());
    return false;
}
```

- 触发条件: `menuType ≠ "F"` + `routeName` (未设置时默认取 `path`) 与 DB 中菜单的 `routeName` 匹配 + `menuType` 与 DB 中菜单的 `menuType` 相同，且需绕过 Sink 1 和 Sink 2
- `{}` 占位符: `routeName` (用户输入的路由名称，未设置时等于 `path`) + `menuName` (DB 中菜单名)
- 注入方式: 设置不同 `parentId` 绕过 Sink 1/2，`routeName` 或 `path` 设为 `sink_attacked`，`menuType` 匹配 DB 中种子菜单的类型
- 可 fuzz: **是**。`routeName` 是用户输入，通过 `{}` 打印到日志。
- Fuzz 结果: **第 2 轮命中**

## 总结

| Sink | 行号 | 条件复杂度 | `{}` 内容 | 可 fuzz | LLM 命中轮次 |
|------|------|-----------|----------|---------|-------------|
| 同级路由冲突 | 386 | menuType≠F + path 匹配 + parentId 匹配 | `dbPath` (种子菜单 path) | **是** | 第 2 轮 |
| 根目录路由冲突 | 391 | menuType≠F + path 匹配 + parentId=0 + dbParentId=0 | `path` (用户直接输入) | **是** | 第 2 轮 |
| 路由名称冲突 | 395 | menuType≠F + routeName 匹配 + menuType 匹配 + 绕过 Sink 1/2 | `routeName` (用户输入) | **是** | 第 2 轮 |

3 个 sink 全部使用 `log.warn` 且日志模板包含 `{}` 占位符，用户可控的 `path`/`routeName` 直接流入日志输出。攻击标记通过请求参数注入到日志中，构成 Log Injection。

LLM Fuzz 结果: **3/3 (100%)**，每个 sink 均在第 2 轮通过偏差反馈+源码片段推理出正确参数组合命中，总耗时 164.5s。

与 MoGuBlog 的对比: MoGuBlog 的 5 个 sink 使用 `ResultUtil.errorWithMessage(MessageConf.XXX)` 返回硬编码常量消息，无 `{}` 占位符，攻击标记无法注入。RuoYi-Cloud-Plus 的 3 个 sink 使用 `log.warn` 且 `{}` 中包含用户可控输入，是真正可利用的 Log Injection 场景。
