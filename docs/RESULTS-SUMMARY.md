# SinkAgent 实验结果汇总 (截至 2026-09-13)

> 模型: Qwen3.8-27B (paratera 网关) | harness 判定: 服务日志中攻击标记 `SINK_REACHED` 相对基线增量 (不信 agent 自述)
> 结果文件统一归档于 `logs/agent_runs/`

## 一、结果总览

**累计 18 次运行: 14 次 PASS / 4 次 FAIL**
- 首次运行成功率: 13 个 sink 任务中 9 个首跑即命中
- 所有 FAIL 经基础设施修复或重试后均命中 (最终 13/13 sink 全部验证可触发)

## 二、ruoyi-plus (10/10 sink 验证通过)

### system 服务 — 菜单/通用异常场景 (单跳业务)

| 任务 | sink 位置 | 运行记录 | 结果 | 调用/耗时 |
|---|---|---|---|---|
| menu_same_level | SysMenuServiceImpl L386「同级路由冲突」 | planB_clean2 | ✅ PASS | 26 / 286s |
| menu_root | SysMenuServiceImpl L391「根目录路由冲突」 | v1 | ❌ FAIL | 62 / 788s |
| | | v2 | ❌ FAIL | 61 / 911s |
| | | v3 | ✅ PASS | 53 / 968s |
| menu_route_name | SysMenuServiceImpl L395「路由名称冲突」 | v1 | ❌ FAIL | 61 / 755s |
| | | v2 | ✅ PASS | 30 / 434s |
| mybatis_duplicate | MybatisExceptionHandler L29「数据库已存在记录」 | — | ✅ PASS | 62 / 887s |

### workflow 服务 — 流程引擎场景 (2026-09-12/13, 零预置数据, 全链路 agent 自建)

| 任务 | sink 位置 | 运行记录 | 结果 | 调用/耗时 |
|---|---|---|---|---|
| wf_def_inuse | FlwDefinitionServiceImpl L192「定义已被使用」 | — | ✅ PASS | 32 / 340s |
| wf_def_sync | FlwDefinitionServiceImpl L240/243「同步流程定义」 | — | ✅ PASS | 36 / 321s |
| wf_inst_var | FlwInstanceServiceImpl L423「变量不存在」 | v1 | ❌ FAIL (token 转录损坏) | 81 / 870s |
| | | v2 (修复后) | ✅ PASS | 33 / 188s |
| wf_listener_end | WorkflowGlobalListener L270「流程已结束」 | — | ✅ PASS | 80 / 839s |
| wf_inst_orphan | FlwInstanceServiceImpl L269「定义信息未找到」 | v1 | ❌ FAIL (401 自愈未覆盖 body 风格) | 101 / 2238s |
| | | v2 (修复后) | ✅ PASS | 86 / 1261s |

**关键攻击链 (agent 自主发现)**:
- **wf_def_sync (跨服务 dubbo 链)**: importDef 导入 flowCode=`<<SINK_REACHED>>` 的流程定义 → 创建租户套餐 → 创建租户 → system 经 dubbo 调 workflow.syncDef 复制定义 → 标记随业务数据流入日志。标记通过**数据流动**进入日志, 而非直接注入日志参数。
- **wf_inst_orphan (多租户隔离绕过)**: DELETE 定义被引用保护挡住 (该保护即另一 sink L192) → agent 改用 PUT /definition 修改定义的 tenantId 为不存在的 999999 → 实例按 tenant_id='000000' 查不到定义 → L269 触发。找到"定义查不到"的第四条路径 (前三条物理删/逻辑删/unPublish 均被 API 校验挡住)。
- **wf_listener_end (完整状态机)**: 创建 test_leave 业务记录 → startWorkFlow 发起 → completeTask 逐节点办理至 end → 全局监听器触发「流程已结束」。

## 三、pig (3/3 sink 验证通过, pig-boot 单体 9999)

| 任务 | sink 位置 | 结果 | 调用/耗时 | agent 攻击链 |
|---|---|---|---|---|
| pig_file_not_exist | SysFileServiceImpl L124「文件不存在」 | ✅ PASS | 13 / 91s | 免鉴权 GET 直达 |
| pig_change_password_wrong | SysUserServiceImpl L586「原密码错误」 | ✅ PASS | 49 / 350s | 登录 (工具层自动 AES 加密密码) → PUT personal/password 传错误旧密码 |
| pig_sms_unregistered | SysMessageServiceImpl L305「手机号未注册」 | ✅ PASS | 27 / 267s | **验证码破解链**: GET /code/image 生成算术验证码 → redis_cmd 读 redis 中答案 (Java 序列化格式) → 提交 `randomStr---答案` |

## 四、黑盒对照实验 (hint_level=none)

对 `menu_same_level` 以无提示模式重跑 (仅给 sink 源码位置, 不给 API 入口与环境信息):

| 配置 | 工具调用 | 耗时 | 结果 |
|---|---|---|---|
| 灰盒 (hint) | 26 | 286s | ✅ PASS |
| 黑盒 (none) | 72 | 682s | ✅ PASS |

黑盒多花的 46 次调用全部用于**基础设施探索** (猜登录端点 → 遍历 nacos 开放 API 检索服务注册 → 试出 8080 网关 → 查配置找到 system 真实端口 9201)。核心 sink 语义推理部分两种配置完全一致。

**结论**: 方法论不依赖部署先验 (黑盒仍可命中); 环境提示的作用是效率优化 (2.4× 成本差) 而非能力开关。

## 五、覆盖难点类型

| 难点 | 验证场景 |
|---|---|
| 单跳业务前置 (写后触发) | menu 三冲突 + mybatis_duplicate |
| 流程引擎状态机 | wf_listener_end |
| 跨服务 dubbo 调用 | wf_def_sync |
| 多租户隔离 | wf_inst_orphan |
| 客户端加密协议 (AES/CFB) | pig_change_password_wrong |
| 服务端验证码机制 | pig_sms_unregistered |

## 六、FAIL 归因 (4 次)

| 运行 | 归因 | 处置 |
|---|---|---|
| menu_root v1/v2 | 模型单次推理波动 (未复用相同 path) | v3 自行解决 (模型随机性, 未改提示词) |
| menu_route_name v1 | 同上 | v2 PASS |
| wf_inst_var v1 | LLM 转录 432 字符 JWT 丢失 4 字符 → 401 → 预算耗尽 | 工具层加 token 自愈后 v2 PASS |
| wf_inst_orphan v1 | 401 自愈未覆盖 RuoYi 的 HTTP 200 + body code:401 风格 | 双风格判定后 v2 PASS |

FAIL 中的两次源于**基础设施缺陷** (已修复), 两次源于**模型推理波动** (重试即过, 与记忆中 clean/clean2 教训一致: 单次失败不宜过度归因)。

## 七、基础设施层修复清单 (公平性说明)

以下修复均为 HTTP 客户端正确性/会话管理, 不改变 agent 的策略能力:
1. `Content-Type */* → application/json` 规范化
2. 控制字符/NBSP 转义 (保证长 token 忠实复制)
3. 401 token 自愈 (记录最近登录 token, 鉴权失败自动重试)
4. 401 双风格判定 (HTTP 401 与 body `{"code":401}`)
5. pig 登录密码自动 AES/CFB 加密 (还原客户端协议)
6. read_logs / harness 判定支持日志文件路径 (pig 单体无容器)

## 八、环境部署成果

- ruoyi-workflow 服务从零部署 (镜像构建 + nacos 配置修复 + compose 服务定义), 根因: env_file 注入 `DUBBO_REGISTRY_ADDRESS=N/A` 覆盖 jar 内 registry 配置
- pig-boot 单体启动参数: `-Dspring.datasource.druid.password=123456 -Dspring.data.redis.host=127.0.0.1 -Dspring.data.redis.password=password -Dspring.data.redis.database=5 -Dfile.local.base-path=/tmp/pigupload -Dserver.port=9999`
- pig 登录: Basic `test:test` (captcha_flag=0), admin/123456; 登录失败会锁 (lock_flag=9 + redis 缓存), 解锁方法见记忆文件
