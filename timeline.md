# 两周实验收官排期（2026-10-08 ～ 2026-10-21）

> 目标：清完 db_state 池剩余 ~220 个 sink（Yudao 114 + 其他项目 ~106），产出完整实验报告数据。
>
> 吞吐测算：单 sink 平均 ~7 分钟 Agent 运行（含 1.3 次重试系数）+ 环境预置，日均 2-3 小时运行量可覆盖 ~20 个/天，总量可行。瓶颈在**服务部署**与**不可达预筛**，不在跑数。

## 当前进度基线（2026-10-13 更新）

| 项目 | db_state 池 | 已验证 | 状态 |
|------|------------|--------|------|
| yudao | 117 | 9 PASS（pay 3 + mall 6）+ 5 静态不可达（pay 域） | trade/pay/system 已部署；**system 阻塞**（WebFrameworkUtils NPE，见 AGENTS.md §5.2） |
| paascloud | 37 | 0 | 未部署（无 Dockerfile） |
| lamp-cloud | 15 | 2 PASS（AbstractTokenGranter L425 登录成功 + PasswordTokenGranter L122 密码输错锁定） | oauth-server(9216) mapper-locations 修复并验证通过 |
| pig | 15 | 6（file_not_exist / change_password / sms 两批 / apikey_token_locked） | 已部署 |
| apollo | 11 | 0 | 未部署 |
| ruoyi-plus | 10 | 10（含 workflow 6 条） | 已部署 |
| small-swarm | 7 | 4（brand create/update/delete/delete_failed） | mall-demo 已部署(9215)；recommend 需 ES |
| 其他散量 | 13 | 0 | — |
| **合计** | **225** | **28** | |

---

## Week 1：Yudao 收官（117 → 0）

| 天 | 日期 | 任务 | 数量 | 前置 |
|---|------|------|-----|------|
| D1 | 10-08 | 提交累积修改 + 更新 RESULTS-SUMMARY + 写**静态可达性预筛脚本**（识别 null-after-throw 死代码模式，pay 家族教训产品化） | — | 半天 |
| D2 | 10-09 | **mall/trade 11 个**（9212 已部署零成本）+ system 9 个 | 20 | 无 |
| D3 | 10-10 | 部署 member + mp → fuzz | 7 | 部署 ~2h |
| D4 | 10-11 | 部署 bpm → fuzz | 13 | 依赖 xxl-job，部署风险最高 |
| D5 | 10-12 | crm/mes/infra + bpm 收尾 | 7 | — |
| D6 | 10-13 | 部署 im → fuzz | 8 | — |
| D7 | 10-14 | 部署 iot（依赖最重）→ fuzz + **M1 周检查点**：Yudao 全模块通过率汇总 | 11 | 预留半天缓冲 |

## Week 2：其他项目（~106）

| 天 | 日期 | 任务 | 数量 |
|---|------|------|-----|
| D8 | 10-15 | **paascloud 部署**（最大单块，镜像/依赖评估） | — |
| D9-10 | 10-16/17 | paascloud 分批 fuzz | 37 |
| D11 | 10-18 | lamp-cloud（复用 compose）+ apollo | 26 |
| D12 | 10-19 | mall-swarm 7 + mogu/gulimall/youlai/zlt 散量 | ~13 |
| D13 | 10-20 | 失败重跑 + 不可达候选人工复核 + 黑盒对照补样 | — |
| D14 | 10-21 | 终版：RESULTS-SUMMARY、图 2-8 更新（`scripts/make_figures.py`）、报告数据定稿、提交 | — |

---

## 关键风险与对策

1. **部署失败**（bpm/iot 依赖重）：每个部署日预留缓冲；pay-server 的三个坑（PayProperties 必填、表缺失、admin-api 路由前缀）已踩平，排查套路可复用
2. **不可达浪费预算**：D1 预筛脚本预期剔除 10-20% 死代码候选（参考 pay 域 5/49 比例），直接省 20+ 次运行
3. **建表缺 SQL**：沿用"MyBatis 报错信息反推列清单"方法；必要时从 DO 类批量生成建表语句
4. **LLM 配额/Key 失效**：监控 403/限流，预留备用 Key（pig 实验同款预案）

## 里程碑

- **M1（D7, 10-14）**：Yudao 117 清零，各模块通过率落图
- **M2（D10, 10-17）**：paascloud 完成，总进度 >70%
- **M3（D14, 10-21）**：全池清零 + 报告数据定稿

## 已验证 sink 台账（持续更新）

### yudao pay（9213, trace-real-yudao-cloud-pay-server）
- ✅ `yudao_pay_demo_order_duplicate_notify` — PayDemoOrderServiceImpl L126（33 调用/369.5s）
- ✅ `yudao_pay_wallet_recharge_duplicate_notify` — PayWalletRechargeServiceImpl L131（30 调用/378.2s）
- ✅ `yudao_pay_wallet_recharge_paid_mismatch` — PayWalletRechargeServiceImpl L135（16 调用/735.3s, v1 PASS; 构造 update-paid 回调传入不同 payOrderId 触发支付单号不匹配分支命中目标日志）
- ✅ `yudao_pay_wallet_recharge_not_found` — PayWalletRechargeServiceImpl L124（29 调用/524.9s, v1 PASS; 构造 update-paid 回调传入不存在的充值单 id 触发充值单不存在分支命中目标日志）
- ✅ `yudao_pay_wallet_recharge_order_not_found` — PayWalletRechargeServiceImpl L315（19 调用/591.0s, v1 PASS; 构造 update-paid 回调传入不存在的支付单 id 触发支付单不存在分支命中目标日志）
- ⚠️ `yudao_pay_wallet_recharge_order_status_not_success` — PayWalletRechargeServiceImpl L322（40 调用/471.2s 预算超限未达，人工 curl 预验证返回 1007008003 并命中目标日志，确认真实可达，归档 v1）
- ✅ `yudao_pay_demo_withdraw_duplicate_notify` — PayDemoWithdrawServiceImpl L143（40 调用/408.6s）
- ⛔ "找不到支付客户端"家族 ×5 — L75/L97/L119/L227/L246 静态不可达（validPayChannel 先抛异常）

### yudao mall（9212, trace-real-yudao-cloud-trade-server）
- ✅ `yudao_mall_brokerage_withdraw_duplicate_notify` — BrokerageWithdrawServiceImpl L248（33 调用/960.2s）
- ✅ `yudao_mall_trade_order_paid_duplicate_notify` — TradeOrderUpdateServiceImpl L292（60 调用/1740.8s）
- ✅ `yudao_mall_trade_order_mismatch` — TradeOrderUpdateServiceImpl L295（35 调用/373.3s）
- ✅ `yudao_mall_trade_order_pay_order_not_found` — TradeOrderUpdateServiceImpl L347（36 调用/624.5s）
- ✅ `yudao_mall_trade_order_pay_order_status_not_success` — TradeOrderUpdateServiceImpl L353（v3 PASS, 40 调用/738.5s）
- ✅ `yudao_mall_trade_order_price_mismatch` — TradeOrderUpdateServiceImpl L359（40 调用/1234.1s）
- ✅ `yudao_mall_trade_after_sale_pay_refund_not_found` — AfterSaleServiceImpl L429（38 调用/991.5s, v1 PASS; 构造 update-refunded 回调触发退款单不存在分支命中目标日志）
- ✅ `yudao_mall_trade_after_sale_refund_status_not_success` — AfterSaleServiceImpl L435（40 调用/735.8s, v1 PASS; 构造 update-refunded 回调触发退款单无结果分支命中目标日志）
- ✅ `yudao_mall_trade_after_sale_refund_id_mismatch` — AfterSaleServiceImpl L447（22 调用/961.1s, v1 PASS; 构造 update-refunded 回调触发退款单不匹配分支命中目标日志）
- ✅ `yudao_mall_brokerage_transfer_status_not_success_or_closed` — BrokerageWithdrawServiceImpl L279（40 调用/1593.4s, v1 PASS; 构造 update-transferred 回调触发转账单未结束分支命中目标日志）
- ✅ `yudao_mall_brokerage_transfer_price_mismatch` — BrokerageWithdrawServiceImpl L286（40 调用/1194.1s, v1 PASS; 构造 update-transferred 回调触发转账金额不匹配分支命中目标日志）
- ✅ `yudao_mall_brokerage_transfer_channel_mismatch` — BrokerageWithdrawServiceImpl L298（28 调用/1149.5s, v1 PASS; 构造 update-transferred 回调触发转账渠道不匹配分支命中目标日志）
- ⚠️ `yudao_mall_brokerage_transfer_order_id_mismatch` — BrokerageWithdrawServiceImpl L292（40 调用/686.8s 预算超限未达，人工 curl 预验证返回 1011008008 并命中目标日志，确认真实可达，归档 v1）
- ⚠️ `yudao_mall_trade_after_sale_refund_price_mismatch` — AfterSaleServiceImpl L441（40 调用/894.0s 预算超限未达，人工 curl 预验证返回 1011000113 并命中目标日志，确认真实可达，归档 v1）
- ⚠️ `yudao_mall_trade_order_merchant_order_id_mismatch` — TradeOrderUpdateServiceImpl L365（40 调用预算超限未达，人工 curl 预验证触发成功并命中目标日志，确认真实可达，归档 v1-v3）
- ⚠️ `yudao_mall_brokerage_withdraw_mismatch` — BrokerageWithdrawServiceImpl L252（40 调用/977.7s 预算超限未达，人工 curl 预验证触发成功并命中目标日志，确认真实可达，归档 v1）
- ⛔ `BrokerageWithdrawServiceImpl L273`「payTransfer 不存在」— 静态不可达死代码。PayTransferApiImpl L42 在 transfer 为空时直接返回 null（HTTP 200 空包），Feign 反序列化后 CommonResult 为 null，在 L271 .getCheckedData() 时直接触发 NullPointerException，永远无法到达 L272-274 的判空日志。

### small-swarm（9215, trace-real-mall-swarm-mall-demo）
- ✅ `small_swarm_brand_create` — DemoController L45（18 调用/322.7s）
- ✅ `small_swarm_brand_update` — DemoController L61（8 调用/111.1s）
- ✅ `small_swarm_brand_delete` — DemoController L75（13 调用/668.7s）
- ✅ `small_swarm_brand_delete_failed` — DemoController L78（16 调用/466.2s）
- ⛔ `EsProductServiceImpl L203 recommend` — 需 Elasticsearch，未部署

### pig（9999, /tmp/pigboot.log）
- ✅ pig_file_not_exist / pig_change_password_wrong / pig_sms_unregistered
- ✅ pig_sms_login_not_exist（78 调用/455s）/ pig_opaque_token_locked（100 调用/558s）
- ✅ `pig_apikey_token_locked` — PigCustomOpaqueTokenIntrospector L163（64 调用/827.4s, v4 PASS; 经历 v1 预算超限、v2 残留缓存阻断、v3 密码试错，最终 v4 完成全自主编排）

### ruoyi-plus（9201/9205）
- ✅ 10 个概念 sink 家族全部验证（menu 同级/根/路由名、mybatis 重复、wf 定义在用/同步/变量/孤儿/监听器）

### lamp-cloud（9216, trace-real-lamp-cloud-lamp-oauth-server）
- ✅ `lamp_oauth_login_success` — AbstractTokenGranter L425「登录成功」（40 调用/381.8s）
- ✅ `lamp_oauth_password_error_locked` — PasswordTokenGranter L122「密码最后一次输错后，解锁时间:」（40 调用/1328.1s, v1 PASS; Agent 自主通过 query_db 发现被锁定的账号 test1，构造 POST /anyTenant/login 登录请求成功命中目标日志）

### apollo（8080, trace-real-apollo-apollo-adminservice）
- ✅ `apollo_delete_app` — AdminService L68「is deleting App:」（18 调用/180.6s, v1 PASS; Agent 自主调用 POST /apps 创建应用，随后 DELETE /apps 携带标记触发命中）
- ✅ `apollo_delete_app_namespace` — AppNamespaceService L188「is deleting AppNamespace, appId:」（9 调用/83.5s, v1 PASS; Agent 自主查库定位已有 AppNamespace，调用 DELETE /apps/{appId}/appnamespaces/{namespaceName} 携带 operator 查询参数与标记成功命中目标日志）

### mogu-blog（8080, trace-real-mogublog-mogu-admin）
- ✅ `mogu_admin_login_password_error` — LoginRestApi L120「管理员密码错误」（28 调用/775.6s, v2 PASS; v1 经历预算超限探索，v2 自主定位管理员账号并传入错误密码与攻击标记触发命中）
- ✅ `mogu_admin_authority_verify_success` — AuthorityVerifyAspect L119「用户拥有操作权限，访问的路径:」（40 调用/533.9s, v2 PASS; v1 探索中向路径拼装分号被 StrictHttpFirewall 拦截，v2 自主调用 POST /auth/login 获取有效 Token，并在请求头携带 Token 成功调用受 @AuthorityVerify 注解保护的 POST /blogSort/getList 接口命中目标日志）

### novel-cloud（8080, trace-real-novel-cloud-novel-book-service）
- ✅ `novel_jwt_parse_failed` — JwtUtils L72「JWT解析失败:」（40 调用/995.4s, v1 PASS; 携带 Authorization 头部请求受拦截接口触发 JWT 校验失败分支）

### SpringBlade（8080/8106, trace-real-springblade-blade-auth）
- ⛔ `PasswordTokenGranter L82 / CaptchaTokenGranter L96`「用户登录失败」— 静态/动态不可达死代码。BladeBeanSerializerModifier 机制将下游 blade-system 返回的 null 对象序列化为 `{}`，经 Feign 反序列化为非空空对象 `new User()`，导致 `userInfo.getUser() == null` 永远不成立；下游故障时亦直接抛 500 异常跳过 fallback。

### RuoYi-Cloud（8080, trace-real-ruoyi-cloud-ruoyi-auth）
- ✅ `ruoyi_auth_token_parse_failed` — TokenService L113「获取用户信息异常」（15 调用/681.8s, v1 PASS; HeaderInterceptor 拦截携带非法 JWT 的 Authorization 请求头，解析抛异常进入 catch 命中目标日志）

### zlt-microservices-platform（8080, trace-real-zlt-microservices-platform-zlt-uaa）
- ✅ `zlt_sms_validate_code_not_expired` — ValidateCodeServiceImpl L62「用户:{}验证码未失效{}」（25 调用/426.2s, v1 PASS; Agent 自主阅读源码定位 Redis 键命名规范 `DEFAULT_CODE_KEY:{mobile}`，通过 redis_cmd 预置缓存状态，随后调用 GET /validata/smsCode/{mobile} 携带 <<SINK_REACHED>> 成功触发目标日志）

### youlai-mall（8803, trace-real-youlai-mall-oms）
- ✅ `youlai_order_submit_params` — OrderServiceImpl L176「订单提交参数:{}」（40 调用/509.6s, v1 PASS; Agent 自主探索 OrderSubmitForm 表单结构与字段约束，构造符合 Bean Validation 规范的提交数据，调用 POST /app-api/v1/orders/submit 并在 remark 等字段中注入 <<SINK_REACHED>> 标记命中目标日志）

### mall4cloud（8080, trace-real-mall4cloud-mall4cloud-auth）
- ✅ `mall4cloud_token_decrypt_error` — TokenStore L264「logger.error(e.getMessage())」（8 调用/645.0s, v1 PASS; Agent 自主阅读 TokenStore 源码定位解密协议规范，构造携带 <<SINK_REACHED>> 的 Base64 编码 refreshToken 负载，调用 POST /ua/token/refresh 触发解密解析异常命中目标日志）

### PiggyMetrics（8080, trace-real-piggymetrics-account-service）
- ✅ `piggymetrics_token_fetch_user_details_failed` — CustomUserInfoTokenServices L132「Could not fetch user details:」（20 调用/623.3s, v1 PASS; Agent 自主阅读 CustomUserInfoTokenServices 与 ResourceServerConfig 源码，发现针对受保护端点（/current）携带 Bearer token 时会触发远程 userinfo 解析校验分支，调用 GET /current 携带标记成功触发目标日志）

### Cloud-Platform（8080, trace-real-cloud-platform-ace-admin）
- ✅ `cloud_platform_auth_require_logging` — AuthController L40「require logging...」（40 调用/830.5s, v1 PASS; Agent 自主阅读 AuthController 源码定位身份认证入口 /jwt/token，构造 POST 请求在用户名中注入 <<SINK_REACHED>> 成功触发目标日志）

### java-microservice（8080, trace-real-java-microservice）
- ✅ `javams_order_evaluate_risk_high` — OrderService L77「VIP大额APP端高风险订单」（7 调用/54.9s, v1 PASS; Agent 自主阅读 OrderService 与 AppController 源码，分析出多层嵌套风控规则组合（level=="vip"、amount>10000、channel.startsWith("app")），构造精确 JSON 请求体调用 POST /api/orders/risk-eval 成功命中目标日志）

