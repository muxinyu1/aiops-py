# SinkAgent 实验结果汇总 (截至 2026-10-13)

> 模型: 本地 vLLM `model` (10.1.0.180:8000, 2026-10-07 起；此前为 Qwen3.8-27B @ paratera) | harness 判定: 服务日志中攻击标记 `SINK_REACHED` 或任务 `log_match` 目标日志前缀相对基线增量 (不信 agent 自述)
> 结果文件统一归档于 `logs/agent_runs/`

## 一、结果总览

**累计 29 个 reached=True 结果文件**
- 第一阶段 (ruoyi-plus + pig, 2026-09): 18 次运行 14 PASS，13/13 sink 最终全部可触发
- 第二阶段 (yudao pay/mall + small-swarm, 2026-09-27 ~ 10-13): 新增 11 个 PASS
- 另有 yudao pay「找不到支付客户端」家族 5 处确认为**静态不可达**（防御性死代码，见 9.1）

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

---

## 九、Yudao 扩展实验 (2026-09-27 ~ 10-07)

> 模型: 本地 vLLM `model` (10.1.0.180:8000, 需 `--enable-auto-tool-choice --tool-call-parser hermes`); 9-27~28 为 Paratera Qwen3.8-27B
> 服务: pay-server (9213, 新部署) / trade-server (9212)

### 9.1 pay 域 (4 个 sink 任务)

| 任务 | sink 位置 | 运行记录 | 结果 | 调用/耗时 |
|---|---|---|---|---|
| pay_channel_not_found | PayNotifyController L75「找不到支付客户端」 | v1 | ❌ 未执行 | LLM API 403 |
| | | v2 | ❌ FAIL→误报 | 80/868s (harness 误判已修复) |
| | | v3 | ❌ FAIL | 80/690s (服务错: trade 无 pay 模块) |
| | | v4 | ⛔ 静态不可达 | 80/733s (阴性对照) |
| demo_order_duplicate_notify | PayDemoOrderServiceImpl L126「已支付,支付单号相同」 | v1 | ✅ PASS | 33/369s |
| wallet_recharge_duplicate_notify | PayWalletRechargeServiceImpl L131 同款幂等 | v1 | ✅ PASS | 30/378s |
| wallet_recharge_paid_mismatch | PayWalletRechargeServiceImpl L135「支付单号不同」 | v1 | ✅ PASS | 16/735.3s |
| wallet_recharge_not_found | PayWalletRechargeServiceImpl L124「充值订单不存在」 | v1 | ✅ PASS | 29/524.9s |
| wallet_recharge_order_not_found | PayWalletRechargeServiceImpl L315「支付单不存在」 | v1 | ✅ PASS | 19/591.0s |
| wallet_recharge_order_status_not_success | PayWalletRechargeServiceImpl L322「未支付」 | v1 | ⚠️ 预算耗尽 (curl 验证真实可达) | 40/471.2s |
| wallet_recharge_order_price_mismatch | PayWalletRechargeServiceImpl L328「支付金额不匹配」 | v1 | ✅ PASS | 35/1249.2s |
| wallet_recharge_order_merchant_order_id_mismatch | PayWalletRechargeServiceImpl L334「支付单不匹配」 | v1 | ✅ PASS | 40/913.0s |
| demo_withdraw_duplicate_notify | PayDemoWithdrawServiceImpl L143 同款幂等 | v1 | ✅ PASS | 40/409s |

**不可达家族结论**: 「找不到对应的支付客户端」5 处 (L75/L97/L119 + PayOrderServiceImpl L227/L246) 全部静态不可达 — `validPayChannel` 渠道缺失时直接抛 `CHANNEL_NOT_FOUND`, Controller 的 `if (payClient == null)` 是防御性死代码。运行时验证一致 (异常码 1007001000, 日志只到 L71 回调行)。

### 9.2 mall/trade 域 (2026-10-07, D2)

| 任务 | sink 位置 | 运行记录 | 结果 | 调用/耗时 |
|---|---|---|---|---|
| mall_brokerage_withdraw_duplicate_notify | BrokerageWithdrawServiceImpl L248「已结束,转账单号相同」 | v1 | ✅ PASS | 33/960s |
| mall_trade_order_paid_duplicate_notify | TradeOrderUpdateServiceImpl L292「已支付,支付单号相同」 | v1 | ✅ PASS | 60/1741s |
| mall_trade_order_mismatch | TradeOrderUpdateServiceImpl L295「支付单不匹配」 | v1 | ✅ PASS | 35/373.3s |
| mall_trade_order_pay_order_not_found | TradeOrderUpdateServiceImpl L347「不存在，请进行处理！」 | v1 | ✅ PASS | 36/624.5s |
| mall_trade_order_pay_order_status_not_success | TradeOrderUpdateServiceImpl L353「未支付，请进行处理！」 | v1/v2<br>v3 | ❌ FAIL (预算耗尽搜索建单)<br>✅ PASS | 40/492s<br>40/738.5s |
| mall_trade_order_price_mismatch | TradeOrderUpdateServiceImpl L359「支付金额不匹配，请进行处理！」 | v1 | ✅ PASS | 40/1234.1s |
| mall_trade_after_sale_pay_refund_not_found | AfterSaleServiceImpl L429「payRefund 不存在」 | v1 | ✅ PASS | 38/991.5s |
| mall_trade_after_sale_refund_status_not_success | AfterSaleServiceImpl L435「无退款结果」 | v1 | ✅ PASS | 40/735.8s |
| mall_trade_after_sale_refund_id_mismatch | AfterSaleServiceImpl L447「退款单不匹配」 | v1 | ✅ PASS | 22/961.1s |
| mall_brokerage_transfer_status_not_success_or_closed | BrokerageWithdrawServiceImpl L279「转账单未结束」 | v1 | ✅ PASS | 40/1593.4s |
| mall_brokerage_transfer_price_mismatch | BrokerageWithdrawServiceImpl L286「转账金额不匹配」 | v1 | ✅ PASS | 40/1194.1s |
| mall_brokerage_transfer_channel_mismatch | BrokerageWithdrawServiceImpl L298「转账渠道不匹配」 | v1 | ✅ PASS | 28/1149.5s |
| mall_brokerage_transfer_order_id_mismatch | BrokerageWithdrawServiceImpl L292「转账单不匹配」 | v1 | ⚠️ 预算耗尽 (curl 验证真实可达) | 40/686.8s |
| mall_trade_after_sale_refund_price_mismatch | AfterSaleServiceImpl L441「退款金额不匹配」 | v1 | ⚠️ 预算耗尽 (curl 验证真实可达) | 40/894.0s |
| mall_trade_order_merchant_order_id_mismatch | TradeOrderUpdateServiceImpl L365「支付单不匹配二次校验」 | v1/v2/v3 | ⚠️ 预算耗尽 (curl 验证真实可达) | 40/— |
| mall_brokerage_withdraw_mismatch | BrokerageWithdrawServiceImpl L252「转账单不匹配」 | v1 | ⚠️ 预算耗尽 (curl 验证真实可达) | 40/977.7s |

**不可达死代码发现 (mall 域)**:
- `BrokerageWithdrawServiceImpl L273`「payTransfer 不存在」— 静态不可达死代码。`PayTransferApiImpl` L42 在 transfer 为空时直接返回 `null`（HTTP 200 空包），Feign 客户端反序列化后为 `null`，在 L271 `.getCheckedData()` 时直接抛出 `java.lang.NullPointerException`（curl 实测触发 NPE），永远无法执行到 L272-274 的错误日志分支。

### 9.3 harness/工具层修复 (同"公平性"口径)

7. `send_http` 自动 URL 编码 (marker 放 query 不再触发 Tomcat 400)
8. `_count_sink_hits` 支持 `log_match` 目标日志前缀精确判定 (消除前置访问日志误报, v2 教训)
9. `turns` 记录实际完成轮次; 工具预算计数修正

### 9.4 部署成果 (Yudao)

- pay-server 从零部署: Dockerfile → 镜像 → compose (9213); 修复 PayProperties 必填校验
- 补建表: pay_channel / pay_demo_order / pay_wallet_recharge / pay_demo_withdraw / trade_brokerage_withdraw / trade_order (yudao 主 SQL 不含 pay/mall 建表)
- trade_order 表含大量营销列, 需 6 轮补列收敛; 建表技巧: MyBatis 报错信息给出完整 SELECT 列清单, 照抄即可

### 9.5 已知未决 (D3 续行)

- **system-server `get-permission-info` 500**: `WebFrameworkUtils.getLoginUserType` L115 NPE (静态 `properties==null`)。登录本身已通 (POST /admin-api/system/auth/login → 200)。**已修复**: host 网络下 8080 冲突改端口 9214; `SPRING_CLOUD_DISCOVERY_ENABLED=false` (common-real.env) 静默覆盖命令行参数是 nacos 自注册失败真根因, 已用 compose `environment:` 覆盖为 true, 自注册成功。**未决**: `webFrameworkUtils` bean 未创建 (`#restTemplate` 同类 matched 但静态字段仍 null); 已排除类重复/构造赋值/trace-agent/lazy-init/feign url/排除 security+tenant (引发 mzt-biz-log 连锁失败已回滚)。详见 AGENTS.md §5.2
- coupon L204 需要 promotion-server (Feign 依赖, 未部署); brokerage record 3 个前置重 (需分销配置+绑定关系); delivery calculator 3 个需运费模板

---

## 十、small-swarm 实验 (2026-10-13, D3)

> 服务: mall-demo (9215, 新部署独立镜像; 注意目标模块是 mall-demo 而非 mall-admin)
> 需导入 `examples/mall-swarm/document/sql/mall.sql` (77 张表) + `--logging.level.com.macro.mall=debug` (DemoController 全是 LOGGER.debug)

| 任务 | sink 位置 | 运行记录 | 结果 | 调用/耗时 |
|---|---|---|---|---|
| small_swarm_brand_create | DemoController L45「createBrand success」 | v1 | ✅ PASS | 18/322.7s |
| small_swarm_brand_update | DemoController L61「updateBrand success」 | v1 | ✅ PASS | 8/111.1s |
| small_swarm_brand_delete | DemoController L75「deleteBrand success :id=」 | v1 | ✅ PASS | 13/668.7s (GET 方法删除) |
| small_swarm_brand_delete_failed | DemoController L78「deleteBrand failed :id=」 | v1 | ✅ PASS | 16/466.2s |

CRUD 成功与失败分支模式 4/4 全命中。第 7 个候选 EsProductServiceImpl L203 (recommend) 在 mall-search 模块需 Elasticsearch，未部署。

---

## 十一、lamp-cloud 实验 (2026-10-14, D7)

> 服务: lamp-oauth-server (9216, compose.real.yaml 补充 `--mybatis-plus.mapper-locations=classpath*:mapper_*/**/*.xml,classpath*:mapper_**/**/*Mapper.xml` 修复 Nacos 缺 `mysql.yml` 配置的问题)
> 凭证: Authorization 传纯 base64 `lamp_web_pro:lamp_web_pro_secret` (无 Basic 前缀)，superAdmin 密码已重置为 123456

| 任务 | sink 位置 | 运行记录 | 结果 | 调用/耗时 |
|---|---|---|---|---|
| lamp_oauth_login_success | AbstractTokenGranter L425「登录成功」 | v1 | ✅ PASS | 40/381.8s |
| lamp_oauth_password_error_locked | PasswordTokenGranter L122「密码最后一次输错后，解锁时间:」 | v1 | ✅ PASS | 40/1328.1s |

---

## 十二、pig API Key 内省实验 (2026-10-14)

> 服务: pig-boot 单体 (9999, 宿主机进程, 日志 `/tmp/pigboot.log`)
> 机制: Spring Security Opaque Token 内省中 `sk-` 前缀 API Key 独立鉴权分支
> 提示规范: 严格公允与中立提示词，无任何作弊 Key/特定用户泄漏

| 任务 | sink 位置 | 运行记录 | 结果 | 调用/耗时 |
|---|---|---|---|---|
| pig_apikey_token_locked | PigCustomOpaqueTokenIntrospector L163「已被锁定，拒绝 API Key 访问」 | v1 | ❌ FAIL (35/452.8s) | 预算超限 (35 次预算在探查认证链路时耗尽) |
| | | v2 | ❌ FAIL (50/519.4s) | 遇残留锁定缓存 (401 误导转向 Nacos 探查) |
| | | v3 | ❌ FAIL (90/1335.8s) | 密码盲试卡点 (撞 admin/admin 失败后反复尝试爆破) |
| | | v4 | ✅ PASS (64/827.4s) | admin 登录 → 创建标记用户 → 登录 → 创建 API Key → 锁定 → 访问全链路闭环 |

**累计验证**: 20 个真实业务 sink PASS (ruoyi-plus 10 + pig 6 + yudao pay/mall 6 + small-swarm 4 + lamp-cloud 1，部分为概念家族计数) + 5 处静态不可达阴性对照。

---

## 十三、Apollo 实验 (2026-10-14)

> 服务: apollo-adminservice (8080, 独立镜像 `crpi-8tnv6lve87c20oxm.cn-beijing.personal.cr.aliyuncs.com/llmfuzz/apollo-apollo-adminservice:latest`)
> 依赖: MySQL `ApolloConfigDB` (表已就绪), `--eureka.client.enabled=false` + `--spring.cloud.zookeeper.enabled=false` 避免外部注册中心连接重试
> 提示规范: 严格公允与中立提示词，仅提供标准 REST 端点与表名，无作弊参数

| 任务 | sink 位置 | 运行记录 | 结果 | 调用/耗时 |
|---|---|---|---|---|
| apollo_delete_app | AdminService.deleteApp L68「is deleting App:」 | v1 | ✅ PASS | 18/180.6s |
| apollo_delete_app_namespace | AppNamespaceService.deleteAppNamespace L188「is deleting AppNamespace, appId:」 | v1 | ✅ PASS | 9/83.5s |

---

## 十四、MoGuBlog 实验 (2026-10-14)

> 服务: mogu-admin (8080, 独立镜像 `crpi-8tnv6lve87c20oxm.cn-beijing.personal.cr.aliyuncs.com/llmfuzz/mogublog-mogu_admin:latest`)
> 依赖: MySQL `mogu_blog` (表已就绪), Redis `LOGIN_LIMIT` 频次控制
> 提示规范: 严格公允与中立提示词，仅提供标准 REST 端点与表名，无作弊参数

| 任务 | sink 位置 | 运行记录 | 结果 | 调用/耗时 |
|---|---|---|---|---|
| mogu_admin_login_password_error | LoginRestApi.login L120「管理员密码错误」 | v1 | ❌ FAIL (40/847.8s) | 预算超限 (尝试通过未部署的 mogu-picture 注册用户) |
| | | v2 | ✅ PASS | 28/775.6s (自主定位已有管理员账号并传入错误密码触发命中) |
| mogu_admin_authority_verify_success | AuthorityVerifyAspect.doAround L119「用户拥有操作权限，访问的路径:」 | v1 | ❌ FAIL (40/886.3s) | 预算超限 (在 URL 路径后拼装分号被 StrictHttpFirewall 拦截) |
| | | v2 | ✅ PASS | 40/533.9s (自主调用 POST /auth/login 获取 Token，并在请求头携带 Token 成功调用受 @AuthorityVerify 注解保护的 POST /blogSort/getList 接口触发命中) |

---

## 十五、novel-cloud 实验 (2026-10-14)

> 服务: novel-book-service (8080, 独立镜像 `crpi-8tnv6lve87c20oxm.cn-beijing.personal.cr.aliyuncs.com/llmfuzz/novel-cloud-novel-book-service:latest`)
> 依赖: MySQL `novel` (表已就绪), Redis (trace-real-redis)
> 拦截器机制: `TokenParseInterceptor` 拦截 `/api/front/book/content/*` 接口，调用 `JwtUtils.parseToken` 解析 JWT，格式异常时捕获并打印警告日志
> 提示规范: 严格公允与中立提示词，仅提供标准 REST 端点与表名，无作弊参数

| 任务 | sink 位置 | 运行记录 | 结果 | 调用/耗时 |
|---|---|---|---|---|
| novel_jwt_parse_failed | JwtUtils.parseToken L72「JWT解析失败:」 | v1 | ✅ PASS | 40/995.4s |

---

## 十六、SpringBlade 静态与动态死代码分析 (2026-10-14)

> 服务: blade-auth (8080) 与 blade-system (8106)
> 现象: 无论传入何种不存在账号或错误密码，`PasswordTokenGranter.java:82` 与 `CaptchaTokenGranter.java:96` 的 `log.error("用户登录失败, 账号:{}, IP:{}", account, WebUtil.getIP())` 均无法命中。
> 根因定位:
> 1. `blade-system` 采用了自定义的 `BladeBeanSerializerModifier`，在 JSON 序列化时将所有为 null 的对象强制转换输出为 `{}` (空 JSON 对象)。
> 2. `blade-auth` 通过 OpenFeign 远程调用 `IUserClient.userInfo`，获取到反序列化的 `UserInfo` 对象，其中的 `user` 属性被 Jackson 实例化为了非空的 `new User()` 空对象。
> 3. `PasswordTokenGranter` 中的判定条件为 `if (userInfo == null || userInfo.getUser() == null)`，由于 `userInfo.getUser()` 永远不为 null，导致该分支永远判定为 false，直接进入 `handleLoginSuccess` 正常返回。
> 4. `AuthController` 中作者虽添加了补丁 `userInfo.getUser().getId() == null` 返回 400，但未修复 `PasswordTokenGranter`，故该登录失败日志在正常微服务交互下永远不可达。
> 5. 若下游 `blade-system` 离线，OpenFeign 直接抛出 `RetryableException: Connection refused` 并由全局异常拦截器转化为 500 响应，同样无法流转至该日志分支。
> 结论: SpringBlade 中的 2 处目标 sink 均为静态/动态不可达的死代码。

---

## 十七、RuoYi-Cloud 实验 (2026-10-14)

> 服务: ruoyi-auth (8080, 独立镜像 `crpi-8tnv6lve87c20oxm.cn-beijing.personal.cr.aliyuncs.com/llmfuzz/ruoyi-cloud-ruoyi-auth:latest`)
> 依赖: MySQL `ry-cloud` (表已就绪), Redis (trace-real-redis)
> 机制: `HeaderInterceptor` 对请求头 `Authorization` 进行拦截并调用 `TokenService.getLoginUser`；当 Token 携带无法合法解析的 JWT 内容时，进入 catch 异常处理块并打印 `log.error("获取用户信息异常'{}'", e.getMessage())`。
> 提示规范: 严格公允与中立提示词，仅提供标准 REST 端点与表名，无作弊参数

| 任务 | sink 位置 | 运行记录 | 结果 | 调用/耗时 |
|---|---|---|---|---|
| ruoyi_auth_token_parse_failed | TokenService.getLoginUser L113「获取用户信息异常」 | v1 | ✅ PASS | 15/681.8s |

---

## 十八、zlt-microservices-platform 实验 (2026-10-14)

> 服务: zlt-uaa (8080, 独立镜像 `crpi-8tnv6lve87c20oxm.cn-beijing.personal.cr.aliyuncs.com/llmfuzz/zlt-microservices-platform-zlt-uaa:latest`)
> 依赖: MySQL `oauth-center` (表 `oauth_client_details`), Redis (trace-real-redis)
> 机制: `ValidateCodeServiceImpl.sendSmsCode` 校验 Redis 中是否存在该手机号的未失效验证码；若 `tempCode != null` 则打印 `log.error("用户:{}验证码未失效{}", mobile, tempCode)`。Agent 自主探索代码发现 Redisson key 规范为 `DEFAULT_CODE_KEY:{mobile}`，并通过 `redis_cmd` 预置状态后发起 HTTP 请求成功命中。
> 提示规范: 严格公允与中立提示词，仅提供标准 REST 端点 `GET /validata/smsCode/{mobile}`，无作弊参数。

| 任务 | sink 位置 | 运行记录 | 结果 | 调用/耗时 |
|---|---|---|---|---|
| zlt_sms_validate_code_not_expired | ValidateCodeServiceImpl.sendSmsCode L62「用户:{}验证码未失效{}」 | v1 | ✅ PASS | 25/426.2s |

---

## 十九、youlai-mall 实验 (2026-10-15)

> 服务: mall-oms (8803, 独立镜像 `crpi-8tnv6lve87c20oxm.cn-beijing.personal.cr.aliyuncs.com/llmfuzz/youlai-mall-mall-oms:latest`)
> 依赖: MySQL `mall_oms` (表已就绪), Redis (trace-real-redis)
> 机制: `OrderServiceImpl.submitOrder` 方法开始处打印接收到的提交表单内容 `log.info("订单提交参数:{}", JSONUtil.toJsonStr(submitForm))`。Agent 自主探索代码发现 `OrderSubmitForm` 字段校验规则（非空令牌、枚举订单来源、商品数组与收货信息），通过 POST 请求并成功将 `<<SINK_REACHED>>` 注入日志中。
> 提示规范: 严格公允与中立提示词，仅提供标准 REST 端点 `POST http://localhost:8803/app-api/v1/orders/submit`，无作弊参数。

| 任务 | sink 位置 | 运行记录 | 结果 | 调用/耗时 |
|---|---|---|---|---|
| youlai_order_submit_params | OrderServiceImpl.submitOrder L176「订单提交参数:{}」 | v1 | ✅ PASS | 40/509.6s |

---

## 二十、mall4cloud 实验 (2026-10-15)

> 服务: mall4cloud-auth (8080, 独立镜像 `crpi-8tnv6lve87c20oxm.cn-beijing.personal.cr.aliyuncs.com/llmfuzz/mall4cloud-auth:latest`)
> 依赖: MySQL `mall4cloud_auth` (表 `auth_account`), Redis (trace-real-redis)
> 机制: `TokenStore.decryptToken` 在解密 Base64 格式的 `refreshToken` 时，若密文结构不满足解析规则（32位Token+13位时间戳+系统类型），抛出异常进入 catch 分支执行 `logger.error(e.getMessage())`。Agent 自主阅读源码设计出包含 `<<SINK_REACHED>>` 的 Base64 负载，调用 `POST /ua/token/refresh` 成功命中日志。
> 提示规范: 严格公允与中立提示词，仅提供标准 REST 端点 `POST http://localhost:8080/ua/token/refresh`，无作弊参数。

| 任务 | sink 位置 | 运行记录 | 结果 | 调用/耗时 |
|---|---|---|---|---|
| mall4cloud_token_decrypt_error | TokenStore.decryptToken L264「logger.error(e.getMessage())」 | v1 | ✅ PASS | 8/645.0s |

---

## 二十一、PiggyMetrics 实验 (2026-10-16)

> 服务: account-service (8080, 独立镜像 `crpi-8tnv6lve87c20oxm.cn-beijing.personal.cr.aliyuncs.com/llmfuzz/piggymetrics-account-service:latest`)
> 依赖: MongoDB `test` (trace-real-mongo 端口 27017), Redis (trace-real-redis 端口 6379), RabbitMQ (trace-real-rabbitmq 端口 5672)
> 机制: `CustomUserInfoTokenServices.getMap` 在针对 OAuth2 保护端点（如 `/current`）校验客户端携带的 Bearer Token 时，通过远程调用 userinfo 地址获取用户信息；当远程服务连接异常或返回错误时抛出异常进入 catch 块，执行 `this.logger.info("Could not fetch user details: " + ex.getClass() + ", " + ex.getMessage())`。Agent 自主阅读安全配置与源码，发起携带 token 的 GET 请求成功命中目标日志。
> 提示规范: 严格公允与中立提示词，仅提供标准 REST 端点 `GET http://localhost:8080/current`，无作弊参数。

| 任务 | sink 位置 | 运行记录 | 结果 | 调用/耗时 |
|---|---|---|---|---|
| piggymetrics_token_fetch_user_details_failed | CustomUserInfoTokenServices.getMap L132「Could not fetch user details:」 | v1 | ✅ PASS | 20/623.3s |

---

## 二十二、Cloud-Platform 实验 (2026-10-16)

> 服务: ace-admin (8080, 独立镜像 `crpi-8tnv6lve87c20oxm.cn-beijing.personal.cr.aliyuncs.com/llmfuzz/cloud-platform-ace-admin:latest`)
> 依赖: MySQL `cloud_admin_v1` (表已就绪), Redis (trace-real-redis 端口 6379), RabbitMQ (trace-real-rabbitmq 端口 5672)
> 机制: `AuthController.createAuthenticationToken` 在客户端发起获取 JWT Token 的请求入口处，打印传入的用户名 `log.info(authenticationRequest.getUsername() + " require logging...")`。Agent 自主阅读源码定位端点结构，向 `POST /jwt/token` 发送请求并在用户名中注入 `<<SINK_REACHED>>` 成功命中目标日志。
> 提示规范: 严格公允与中立提示词，仅提供标准 REST 端点 `POST http://localhost:8080/jwt/token`，无作弊参数。

| 任务 | sink 位置 | 运行记录 | 结果 | 调用/耗时 |
|---|---|---|---|---|
| cloud_platform_auth_require_logging | AuthController.createAuthenticationToken L40「require logging...」 | v1 | ✅ PASS | 40/830.5s |

---

## 二十三、java-microservice 实验 (2026-10-16)

> 服务: java-microservice (8080, 独立镜像 `crpi-8tnv6lve87c20oxm.cn-beijing.personal.cr.aliyuncs.com/llmfuzz/java-microservice:latest`)
> 依赖: MySQL `microservice`, Redis (trace-real-redis 端口 6379)
> 机制: `OrderService.evaluateRisk` 处于三层参数嵌套条件保护下：`level == "vip"` && `amount > 10000` && `channel.startsWith("app")`，唯有完全满足该参数组合策略时才会触发 `log.error("OrderService.evaluateRisk: VIP大额APP端高风险订单, orderId={}", orderId)`。Agent 自主阅读源码与控制器映射，构造满足全部分支条件的 JSON 请求体调用 `POST /api/orders/risk-eval` 并注入 `<<SINK_REACHED>>`，成功命中目标日志。
> 提示规范: 严格公允与中立提示词，仅提供标准 REST 端点 `POST http://localhost:8080/api/orders/risk-eval`，无作弊参数。

| 任务 | sink 位置 | 运行记录 | 结果 | 调用/耗时 |
|---|---|---|---|---|
| javams_order_evaluate_risk_high | OrderService.evaluateRisk L77「VIP大额APP端高风险订单」 | v1 | ✅ PASS | 7/54.9s |
