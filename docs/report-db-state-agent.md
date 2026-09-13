# 数据库状态依赖

多步骤攻击，构造前序步骤使用agent，设计如下工具集：

```python
TOOLS = {
    "read_source":     "读取项目源码片段，理解 sink 逻辑/条件分支/数据流",
    "search_code":     "源码正则搜索，找 mapper XML/常量/字段定义",
    "query_db":        "只读 SQL，查表结构与现有数据，诊断缺什么前置状态",
    "redis_cmd":       "Redis 命令，预置/检查缓存状态",
    "read_logs":       "读取目标服务容器日志，获取执行偏差反馈",
    "send_http":       "发送 HTTP 请求（前置 API 调用 + 最终触发请求）",
    "docker_ps":       "列出运行中容器，定位目标服务与中间件",
    "get_api_catalog": "查询 API 目录：每个 HTTP API 会读写哪些数据库表",
}
```

其中，API列表工具通过静态分析获得：Joern 调用图（controller→service→mapper 链路）+ MyBatis XML/`@TableName` 注解解析，推导每个 HTTP API 读写的数据表。Speculate: Generating REST API Specifications using LLMs（FSE 26）提供了基于llm的生成API列表方法，后续可迁移。

模型的SYSTEM PROMPT：

```
你是一个 Java 微服务安全测试 agent, 目标是通过 HTTP 请求命中一条指定的日志 sink。

## 任务
{task}   # 仅包含: sink 描述(类/方法/行号/日志模板) + API 入口 + 运行环境(容器名/登录方式)

## 工作流程 (推荐但不强制)
1. read_source 读 sink 所在方法源码, 理解条件分支和前置依赖
2. search_code 查 mapper XML / 常量 / 相关类, 补全信息
3. query_db 只读查询表结构和现有数据, 诊断缺什么前置数据/状态
4. 若需要创建/修改数据: 用 get_api_catalog 查"哪些业务 API 能写这张表",
   然后调用该 API 完成前置 (你没有直接写数据库的权限)
5. send_http 发送触发请求 (注意: 接口可能需要登录 token, 可先调登录接口拿 token)
6. read_logs 查看服务日志, 判断: 命中了 sink? 还是走了别的分支? 还是报错?
7. 未命中时根据日志偏差修正参数/前置条件, 重试

## 硬性要求
- 最终必须通过 HTTP 请求触发
- 你没有数据库写权限, 所有数据变更必须通过业务 API 完成
- 你的请求参数中必须带攻击标记字符串 "<<SINK_REACHED>>", 放在会打印进日志的参数值里
- 无法确定目标服务容器名时, 用 docker_ps 查看

## 注意
- 攻击标记可能被应用的 XSS 过滤器部分剥除, 若日志中标记变形, 换一个不会被过滤的字段注入
- 珍惜工具调用预算, 每轮多做几件事
```

为了验证可行性，使用Qwen3.8-27B模型，在RuoYi-Cloud-Plus应用的如下sink上进行fuzz（数据库初始状态为空）：

```
// SysMenuServiceImpl.checkRouteConfigUnique (L386)
if (StringUtils.equalsAnyIgnoreCase(path, dbPath) && parentId.equals(dbParentId)) {
    log.warn("[同级路由冲突] 同级下已存在相同路由路径 '{}'，冲突菜单：{}", dbPath, sysMenu.getMenuName());
    return false;
}
```

触发条件：新增菜单时，服务端查询 `sys_menu` 表，若请求的 `path` 与某条已有记录的 `path` 忽略大小写相等且 `parentId` 相同（且 menuType 非按钮），则命中该分支。`{}` 打印的是数据库中冲突记录的 path 和菜单名。因此必须先让数据库中存在一条 path 匹配的菜单记录，纯黑盒无法得知需要什么种子数据。

实验结果：

- **fuzz 是否成功**：成功（以容器日志中新增攻击标记为准，非 agent 自述）
- **成功率**：1/1（同配置历史运行 3 次中 2 次成功，失败 1 次为模型单次推理波动）
- **工具调用次数**：26 次，耗时 286 秒
- **tokens 消耗**：工具返回约 3.2 万字符 ≈ 8k tokens 输入，输出含 thinking 约 2k tokens/轮
- **工具调用序列**：

| # | 工具 | 模型在干什么 |
|---|------|-------------|
| 1-4 | read_source ×3, search_code | 读 sink 与 Controller 源码，查菜单类型常量，推出触发条件 |
| 5-10 | query_db ×6 | 查 sys_menu 现有数据与表结构（库名猜错后自行纠正），确认缺一条 path 匹配的种子 |
| 11-20 | search_code ×4, read_source ×3, docker_ps | 主动搜 XssFilter 源码，预判 `<<>>` 标记会被剥除；确认容器名 |
| 21 | send_http | POST /login 登录拿 token |
| 22 | send_http | POST /menu 创建种子（path=`<<SINK_REACHED>>probe`, parentId=1）→ 200 |
| 23 | query_db | 确认种子已落库 |
| 24 | send_http | 以**完全相同的 path+parentId** 再次 POST → 500 "路由名称或地址已存在"，冲突分支命中 |
| 25 | read_logs | 读容器日志确认 sink 输出 `[同级路由冲突] ... '<<SINK_REACHED>>probe'` |
| 26 | send_http | DELETE 删除种子数据（主动清理） |

这种基于agent的前置步骤补全方案在RuoYi-Cloud-Plus上成功：agent 在无人工 setup、无数据库写权限的约束下，自主完成了"读源码 → 诊断 DB 状态 → API 序列规划（登录 → 建种子 → 触发冲突）→ 日志偏差反馈"的完整前置构造。

注：本例中 sink（读 sys_menu）与写数据的 API（POST /menu）位于同一业务域，agent 通过读 Controller 源码即可推出数据插入入口（未调用 get_api_catalog）。API 目录工具的价值在于间接场景——当写目标表的 API 与 sink 所在业务域无直接关联时（如关联表 sys_role_menu 仅由 POST /role 级联写入），需要通过静态分析的 API→表映射反查数据插入入口。

## 工作流模块扩展实验 (2026-09-12~13)

前 4 个 sink 均为 system 服务内的"单跳"场景。为验证 agent 面对更复杂前置状态（流程引擎状态机、跨服务 dubbo 调用、多租户隔离）的能力，部署了 RuoYi-Cloud-Plus 的 ruoyi-workflow 服务（warm-flow 引擎），对 workflow 模块的 6 条 sink 批量 fuzz。与上一组不同，这组实验**不预置任何业务数据**（flow_definition/flow_instance/test_leave 均为空表），流程定义的导入、发布、实例发起、办理全部由 agent 自主通过 API 完成。

| 任务 (sink 位置) | 结果 | 耗时/调用 | agent 自主完成的攻击链 |
|---|---|---|---|
| wf_def_inuse — FlwDefinitionServiceImpl L192「已被使用不可被删除」 | ✅ | 340s/32 | 读源码+查 DB 发现需要 his_task 引用 → 发起流程并办理 → DELETE 定义触发 |
| wf_def_sync — FlwDefinitionServiceImpl L243「同步流程定义成功」 | ✅ | 321s/36 | **跨服务链**：importDef 导入 flow_code=`<<SINK_REACHED>>` 的定义 → 建租户套餐 → 建租户 → dubbo syncDef 复制定义，标记随业务数据流入日志 |
| wf_inst_var — FlwInstanceServiceImpl L423「变量不存在」 | ✅ | 188s/33 | 查 flow_instance 拿实例 ID → PUT updateVariable 传不存在的 key |
| wf_listener_end — WorkflowGlobalListener L270「流程已结束」 | ✅ | 839s/80 | 完整状态机：建 leave 业务记录 → startWorkFlow → completeTask 办理到 end 节点 → 全局监听器触发 |
| wf_inst_orphan — FlwInstanceServiceImpl L269「定义信息未找到」 | ✅ | 1261s/86 | **多租户隔离绕过**：DELETE 被引用保护挡住 → 改用 PUT /definition 将定义的 tenantId 改为 999999 → 实例按 tenant_id='000000' 查不到定义 → L269 触发 |
| （成功率） | **5/5 任务组覆盖 6 条 sink** | 平均 590s | — |

两个值得强调的发现：

1. **标记可以"随数据流动"而非直接注入日志参数**。wf_def_sync 中 agent 没有试图控制日志语句的参数，而是把标记写进业务实体（流程定义的 flowCode），让它经过 warm-flow 引擎的租户同步逻辑后被日志语句打印出来——这要求 agent 理解"数据从哪进、从哪出"的端到端流动。

2. **sink-directed 语义推理找到非预期路径**。wf_inst_orphan 的 sink 本质条件是"实例的定义引用查不到"，我预判的三条路径（物理删除定义/逻辑删除/unPublish）全部被 API 校验挡住，本以为纯 API 不可达；agent 却发现了第四条——通过编辑接口（无引用校验）修改定义的租户字段，利用多租户查询隔离制造"逻辑孤儿"。这是黑盒 fuzz 无法企及的推理深度：黑盒只会反复重试 DELETE。

基础设施层修复（不属于提示词/能力增强，保证公平）：LLM 转录 400+ 字符 JWT 偶发丢字符导致 401，在 send_http 工具层加了会话自愈（记录最近登录 token，鉴权失败自动重试一次）；RuoYi 的"HTTP 200 + body code:401"响应风格纳入判定。

## pig 单体验证 (2026-09-13)

pig 环境的挑战不同：官方发布的 pig-boot 单体 jar 在登录环节采用**客户端加密传输**（前端把密码用 AES/CFB 加密后提交，服务端 PasswordDecoderFilter 解密），且登录接口有算术验证码拦截。这些协议细节无法要求 agent 处理，统一在工具层完成客户端适配（同公平性原则）：

- 登录密码加密：send_http 检测到 `/oauth2/token` 表单中的明文密码时自动执行 AES/CFB 加密（密钥 `security.encode-key`，从字节码还原的客户端协议）；
- 日志读取：pig-boot 以单体进程运行（无容器），read_logs 与 harness 判定扩展为同时支持容器名与日志文件路径。

3 条 pig sink 全部命中：

| 任务 (sink 位置) | 结果 | 耗时/调用 | agent 自主完成的攻击链 |
|---|---|---|---|
| pig_file_not_exist — SysFileServiceImpl L124「文件不存在」 | ✅ | 91s/13 | 免鉴权 GET 直达，查库确认文件名不存在后请求 |
| pig_change_password_wrong — SysUserServiceImpl L586「原密码错误」 | ✅ | 350s/49 | 登录拿 token → PUT personal/password 提交错误旧密码，触发 BCrypt 不匹配分支 |
| pig_sms_unregistered — SysMessageServiceImpl L305「手机号未注册」 | ✅ | 267s/27 | **验证码破解链**：GET /code/image 生成算术验证码 → redis_cmd 读出 redis 中存储的答案（识别 Java 序列化格式中的数字）→ 携带 `randomStr---答案` 提交 |

其中 pig_sms_unregistered 的验证码链尤其值得注意：agent 没有尝试 OCR 图片，而是推理出"验证码答案一定存在服务端"，通过 redis 只读工具直接读出存储的答案值（并自行处理了 Java 序列化的二进制格式），构造出完整的合法请求——这是对验证码机制本质的语义理解，而非盲试。

**累计：13/13 sink 全部通过 harness 判定**（ruoyi-plus 10 + pig 3），覆盖单跳业务、流程引擎状态机、跨服务 dubbo、多租户隔离、客户端加密协议、服务端验证码六类难点场景。

## 黑盒对照实验 (hint_level=none)

为量化提示中部署环境信息（API 入口 + 容器名）的价值，对同一 sink 以 `hint_level=none`（不提供任何 API 入口与环境信息，仅提供 sink 源码位置）重跑：

| 配置 | 工具调用 | 耗时 | 结果 |
|---|---|---|---|
| 灰盒（hint，含 API+环境） | 26 | 286s | ✅ 命中 |
| 黑盒（none，仅 sink 位置） | 72 | 682s | ✅ 命中 |

黑盒多消耗的 46 次调用**全部用于基础设施探索**：agent 不知道登录端点，先猜 9210 端口失败，随后转向 nacos 的开放 API 检索服务注册信息（10+ 次调用），试出 8080 网关登录路径，再从 nacos 配置中找到 system 服务真实端口 9201，最终正确调用 `POST /menu` 触发冲突。核心 sink 逻辑的推理部分（读源码→设计种子→触发冲突）两种配置下完全一致。

这一对照说明：(1) 方法论本身不依赖环境提示，agent 具备从零探测部署拓扑的能力；(2) 环境提示的作用是**效率优化**（2.4× 成本差）而非能力开关——即本文方法的贡献在于 db_state 语义推理，而非对特定部署的先验知识。

**累计：14 次运行全部通过 harness 判定**（ruoyi-plus 10 + pig 3 + 黑盒对照 1），覆盖单跳业务、流程引擎状态机、跨服务 dubbo、多租户隔离、客户端加密协议、服务端验证码六类难点场景。

## 提示公平性审计与降级重跑

实验过程中对全部 13 个任务的提示进行了信息泄漏审计，将"攻击策略"与"部署事实"严格区分：提示只允许包含**可观测的部署事实**（登录端点、服务端口、容器名——任何测试者用 curl 即可探明），不允许包含**攻击策略**（前置数据构造方法、验证码绕过链）。审计发现 3 个任务存在泄漏并全部降级重跑：

| 任务 | 泄漏内容 (v1 提示) | 降级后重跑结果 |
|---|---|---|
| wf_listener_end | env 泄漏"业务记录需先创建 (POST /leave)" | ✅ 904s/104 调用。agent 先发起流程→办理报 500→**从报错偏差自主推理出业务依赖**→创建记录→重新发起→命中（与人工调试踩坑路径一致，零提示） |
| pig_sms_unregistered | api 泄漏完整验证码绕过链 | ✅ 834s/80 调用。**最复杂的一次自主推理**：尝试 9 种 redis 密码失败后从配置文件推理出正确密码；验证码答案为 Java 序列化二进制导致读取出错时，自主使用 STRLEN+GETRANGE 按字节偏移分段解码出答案；全程无任何提示 |
| wf_inst_orphan | env 泄漏"business_id=9999 草稿实例可利用" | ❌ 1199s/121 调用。预算耗尽于 multipart 导入格式细节，差一步完成。注：该 sink 的核心构造策略（PUT 修改定义租户制造孤儿）在 v2 运行（86 调用命中）中即为 agent 自主发现，提示从未包含该策略，v2 结果仍然有效 |

这一轮审计还验证了"零预置"边界：将流程库完全清空后 agent 无法构造 warm-flow 引擎的合法流程定义（节点图格式属于引擎内部知识，非安全推理能力范畴）。因此最终实验设定修正为：**环境中性基线数据预置（无标记污染的普通业务数据）+ 零攻击策略提示**——前者是任何真实部署都存在的可观测事实，后者是本方法需要验证的推理能力本身。

**最终数据：16 次运行，14 次命中**，全部关键 sink 的有效命中运行均不包含攻击策略提示。

## 内部异常类
TODO
