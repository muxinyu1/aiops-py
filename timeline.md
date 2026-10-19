# 两周实验收官排期（2026-10-08 ～ 2026-10-21）

> 目标：清完 db_state 池剩余 ~220 个 sink（Yudao 114 + 其他项目 ~106），产出完整实验报告数据。
>
> 吞吐测算：单 sink 平均 ~7 分钟 Agent 运行（含 1.3 次重试系数）+ 环境预置，日均 2-3 小时运行量可覆盖 ~20 个/天，总量可行。瓶颈在**服务部署**与**不可达预筛**，不在跑数。

## 当前进度基线（2026-10-19 更新）

| 项目 | db_state 池 | 已验证 | 状态 |
|------|------------|--------|------|
| yudao | 117 | 55 PASS（pay 34 + mall 16 + system 5）+ 15 静态/环境不可达 | pay/trade(mall) 候选已全部 100% 收敛完成，system 核心候选全部通过，其余死代码/微信/未部署依赖已证明不可达 |
| paascloud | 37 | 0 | 未部署（MySQL 8 驱动依赖已修复，待编写 Dockerfile 部署） |
| lamp-cloud | 15 | 9 PASS（login_success, password_error_locked, refresh_token, user_status, password_error_num, visible_router_default, visible_router_vben5, visible_router_soybean, base_role_resource_set） | oauth-server(9216) 核心链路与可见路由树全线打通 |
| pig | 15 | 6 PASS + 1 静态不可达（isExpired 死代码） | 已部署 (9999) |
| apollo | 11 | 5 PASS（delete_app, delete_app_namespace, admin_invalid_access_token, app_access_key_auto_provision_failed, biz_config_invalid_override_value） | 已部署 (8080)，adminservice 可达候选已全部 100% 收敛完成 |
| ruoyi-plus | 10 | 10 PASS（含 workflow 6 条） | 已部署 (9201/9205) |
| small-swarm | 7 | 5 PASS + 2 静态/环境不可达 | mall-demo (9215) 已全池 7 个收敛清零（5 PASS + 1 死代码 + 1 需ES） |
| 其他散量 | 13 | 10 PASS + 1 静态不可达 | mogu(2)/novel(1)/ruoyi-cloud(1)/zlt(1)/youlai(1)/mall4cloud(1)/piggymetrics(1)/cloud-platform(1)/javams(1) 各 1-2 PASS；SpringBlade 1 静态死代码 |
| **合计** | **225** | **100 PASS + 19 静态/环境/死代码不可达** | **全池已收敛 119 个 sink（累计 103 验证概念目标，107 个 reached 结果文件）** |

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
- ✅ `yudao_pay_demo_order_not_found` — PayDemoOrderServiceImpl L119（36 调用/888.4s, v1 PASS; 构造 update-paid 回调传入不存在的订单 id 触发示例订单不存在分支命中目标日志）
- ✅ `yudao_pay_demo_order_paid_mismatch` — PayDemoOrderServiceImpl L130（31 调用/1333.9s, v1 PASS; 构造 update-paid 回调传入不匹配的 payOrderId 触发支付单不匹配分支命中目标日志）
- ✅ `yudao_pay_demo_order_refund_mismatch` — PayDemoOrderServiceImpl L239（33 调用/873.0s, v1 PASS; 构造 update-refunded 回调传入不匹配的 payRefundId 触发退款单不匹配分支命中目标日志）
- ✅ `yudao_pay_demo_order_refund_price_mismatch` — PayDemoOrderServiceImpl L255（34 调用/734.2s, v1 PASS; 构造 update-refunded 回调传入金额不匹配的退款单 id 触发退款金额不匹配分支命中目标日志）
- ✅ `yudao_pay_demo_order_refund_order_mismatch` — PayDemoOrderServiceImpl L261（27 调用/790.3s, v1 PASS; 构造 update-refunded 回调传入商户退款单号不匹配的退款单 id 触发退款单不匹配二次校验分支命中目标日志）
- ✅ `yudao_pay_order_create_order_exists` — PayOrderServiceImpl L124（13 调用/269.8s, v1 PASS; 调用 RPC createOrder 接口传入已存在的商户订单号触发重复支付单分支命中目标日志）
- ✅ `yudao_pay_wallet_client_order_status_invalid` — WalletPayClient L123（40 调用/1297.0s, v1 PASS; 构造 app-api get order 并设置 sync=true 触发未支付状态同步异常分支命中目标日志）
- ✅ `yudao_pay_order_submit_extension_already_paid` — PayOrderServiceImpl L220（17 调用/404.7s, v1 PASS; 构造 app-api submit order 且对应拓展单已为支付成功状态触发拓展单已支付告警分支命中目标日志）
- ✅ `yudao_pay_wallet_recharge_duplicate_notify` — PayWalletRechargeServiceImpl L131（30 调用/378.2s）
- ✅ `yudao_pay_wallet_recharge_paid_mismatch` — PayWalletRechargeServiceImpl L135（16 调用/735.3s, v1 PASS; 构造 update-paid 回调传入不同 payOrderId 触发支付单号不匹配分支命中目标日志）
- ✅ `yudao_pay_wallet_recharge_not_found` — PayWalletRechargeServiceImpl L124（29 调用/524.9s, v1 PASS; 构造 update-paid 回调传入不存在的充值单 id 触发充值单不存在分支命中目标日志）
- ✅ `yudao_pay_wallet_recharge_order_not_found` — PayWalletRechargeServiceImpl L315（19 调用/591.0s, v1 PASS; 构造 update-paid 回调传入不存在的支付单 id 触发支付单不存在分支命中目标日志）
- ✅ `yudao_pay_wallet_recharge_order_status_not_success` — PayWalletRechargeServiceImpl L322（37 调用/453.0s, v2 PASS; 构造 update-paid 回调传入未支付状态支付单 id=500 触发支付单状态不是【支付成功】分支命中目标日志）
- ✅ `yudao_pay_wallet_recharge_order_price_mismatch` — PayWalletRechargeServiceImpl L328（35 调用/1249.2s, v1 PASS; 构造 update-paid 回调传入金额不匹配的支付单 id 触发支付金额不匹配分支命中目标日志）
- ✅ `yudao_pay_wallet_recharge_order_merchant_order_id_mismatch` — PayWalletRechargeServiceImpl L334（40 调用/913.0s, v1 PASS; 构造 update-paid 回调传入商户订单号不匹配的支付单 id 触发支付单不匹配分支命中目标日志）
- ✅ `yudao_pay_wallet_recharge_refund_id_mismatch` — PayWalletRechargeServiceImpl L259（23 调用/514.5s, v2 PASS; 构造 update-refunded 回调传入不匹配的退款单号触发退款单不匹配分支命中目标日志）
- ✅ `yudao_pay_wallet_recharge_refund_not_found` — PayWalletRechargeServiceImpl L267（24 调用/541.2s, v1 PASS; 构造 update-refunded 回调传入不存在的退款单 id 触发退款单不存在分支命中目标日志）
- ✅ `yudao_pay_wallet_recharge_refund_price_mismatch` — PayWalletRechargeServiceImpl L272（23 调用/867.7s, v1 PASS; 构造 update-refunded 回调传入金额不匹配的退款单 id 触发退款金额不匹配分支命中目标日志）
- ✅ `yudao_pay_wallet_recharge_refund_order_mismatch` — PayWalletRechargeServiceImpl L278（15 调用/622.5s, v1 PASS; 构造 update-refunded 回调传入商户退款单号不匹配的退款单 id 触发退款单不匹配分支命中目标日志）
- ✅ `yudao_pay_wallet_recharge_refund_record_not_found` — PayWalletRechargeServiceImpl L197（40 调用/1186.9s, v1 PASS; 调用 refund 接口传入不存在的充值记录 id 触发充值记录不存在分支命中目标日志）
- ✅ `yudao_pay_wallet_recharge_update_refund_not_found` — PayWalletRechargeServiceImpl L227（16 调用/331.5s, v1 PASS; 构造 update-refunded 回调传入不存在的充值记录 id 触发充值记录不存在分支命中目标日志）
- ✅ `yudao_pay_demo_withdraw_duplicate_notify` — PayDemoWithdrawServiceImpl L143（40 调用/408.6s）
- ✅ `yudao_pay_demo_withdraw_not_found` — PayDemoWithdrawServiceImpl L135（9 调用/228.4s, v1 PASS; 构造 update-transferred 回调传入不存在的提现单 id 触发提现单不存在分支命中目标日志）
- ✅ `yudao_pay_demo_withdraw_mismatch` — PayDemoWithdrawServiceImpl L147（27 调用/593.7s, v1 PASS; 构造 update-transferred 回调传入不匹配的 payTransferId 触发转账单不匹配分支命中目标日志）
- ✅ `yudao_pay_wallet_client_transfer_status_invalid` — WalletPayClient L247（35 调用/677.0s, v1 PASS; 调用同步转账接口 GET /app-api/pay/transfer/sync?id=705 触发钱包客户端转账单状态非正常校验分支命中目标日志）
- ✅ `yudao_pay_transfer_sync_closed` — PayTransferServiceImpl L246（31 调用/577.7s, v2 PASS; 调用 GET /app-api/pay/transfer/sync?id=706 触发关闭状态无需更新分支命中目标日志）
- ✅ `yudao_pay_transfer_sync_progressing` — PayTransferServiceImpl L167（9 调用/115.4s, v1 PASS; 调用 GET /app-api/pay/transfer/sync?id=707 触发转账中状态无需更新分支命中目标日志）
- ✅ `yudao_pay_transfer_sync_success_already` — PayTransferServiceImpl L216（21 调用/274.9s, v1 PASS; 调用 GET /app-api/pay/transfer/sync?id=708 触发成功状态无需更新分支命中目标日志）
- ✅ `yudao_pay_order_sync_extension_success` — PayOrderServiceImpl L332（20 调用/396.1s, v1 PASS; 构造 mock 渠道同步订单 GET /app-api/pay/order/get?id=520&sync=true 触发拓展单更新为已支付分支命中目标日志）
- ✅ `yudao_pay_order_sync_order_success` — PayOrderServiceImpl L372（15 调用/266.0s, v1 PASS; 构造 mock 渠道同步订单 GET /app-api/pay/order/get?id=521&sync=true 触发主订单更新为已支付分支命中目标日志）
- ✅ `yudao_pay_wallet_reduce_wallet_not_found` — PayWalletServiceImpl L150（17 调用/381.4s, v1 PASS; 预置关联非法钱包 wallet_id=99999 的充值单，向 /admin-api/pay/wallet-recharge/update-refunded 回调触发扣减余额时用户钱包不存在分支命中目标日志）
- ✅ `yudao_pay_wallet_add_wallet_not_found` — PayWalletServiceImpl L197（12 调用/250.7s, v1 PASS; 预置关联非法钱包 wallet_id=88888 的充值单，向 /admin-api/pay/wallet-recharge/update-paid 回调触发增加余额时用户钱包不存在分支命中目标日志）
- ⛔ "找不到支付客户端"家族 ×8 — PayNotifyController L75/L97/L119 + PayOrderServiceImpl L227/L246 + PayRefundServiceImpl L102 + PayTransferServiceImpl L73/L312 静态不可达（validPayChannel 先抛异常，防御性死代码）
- ⛔ "用户钱包不存在" — PayWalletController L61 静态不可达（getOrCreateWallet 自动加锁创建新钱包返回，永不为 null）
- ⛔ "支付订单拓展单防御逻辑" ×5 — PayOrderServiceImpl L319/L353/L388/L393/L407 静态不可达/防御性死代码（syncOrderQuietly 过滤 WAITING 状态导致已支付分支不可达；syncOrder 显式拦截 closed 状态无法进入 notifyOrderClosed；mock/wallet 渠道不支持支付回调 webhook）
- ⛔ "退款单状态不正确" — WalletPayClient L179 仅由后台定时任务 PayRefundSyncJob 调用，无 HTTP API 映射入口

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
- ✅ `yudao_mall_brokerage_withdraw_mismatch` — BrokerageWithdrawServiceImpl L252（60 调用/1236.1s, v2 PASS; 构造 update-transferred 回调传入不匹配的转账单 id 触发转账单不匹配分支命中目标日志）
- ✅ `yudao_mall_brokerage_transfer_order_id_mismatch` — BrokerageWithdrawServiceImpl L292（31 调用/885.1s, v3 PASS; 构造 update-transferred 回调传入关联 merchantTransferId 不一致的转账单 id=702 触发商户订单不匹配分支命中目标日志）
- ✅ `yudao_mall_trade_after_sale_refund_price_mismatch` — AfterSaleServiceImpl L441（24 调用/634.0s, v2 PASS; 构造 update-refunded 回调传入金额不匹配的退款单 id=601 触发退款金额不匹配分支命中目标日志）
- ✅ `yudao_mall_trade_order_merchant_order_id_mismatch` — TradeOrderUpdateServiceImpl L365（33 调用/1201.4s, v5 PASS; 构造 update-paid 回调传入关联 merchantOrderId 不一致的支付单 id=502 触发支付单不匹配二次校验分支命中目标日志）
- ⛔ `BrokerageWithdrawServiceImpl L273`「payTransfer 不存在」— 静态不可达死代码。PayTransferApiImpl L42 在 transfer 为空时直接返回 null（HTTP 200 空包），Feign 反序列化后 CommonResult 为 null，在 L271 .getCheckedData() 时直接触发 NullPointerException，永远无法到达 L272-274 的判空日志。

### yudao system（9214, trace-real-yudao-cloud-yudao-module-system-server）
- ✅ `yudao_router_orphan` — AuthConvert L68「找不到父资源」（30 调用/964.4s, v2 PASS; 修复 tool_query_db 正则词界拦截 bug，Agent 自主调用登录 POST /admin-api/system/auth/login，调用 PUT /admin-api/system/menu/update 构造孤儿菜单拓扑，再调用 GET /admin-api/system/auth/get-permission-info 触发菜单树构建缺失父节点分支命中目标日志）
- ✅ `yudao_system_notify_template_disabled` — NotifySendServiceImpl L50「模版已经关闭」（13 调用/238.2s, v1 PASS; Agent 自主调用登录与 POST /admin-api/system/notify-template/create 构造包含标记的关闭状态模板，再调用 send-notify 触发关闭模版分支命中目标日志）
- ✅ `yudao_system_dept_data_permission` — PermissionServiceImpl L332「部门数据权限无法处理」（28 调用/887.6s, v1 PASS; Agent 自主阅读 PermissionServiceImpl 源码分析数据范围枚举，发现用户 104 关联非法范围角色 data_scope=99，调用 RPC GET /rpc-api/system/permission/get-dept-data-permission?userId=104 命中目标日志）
- ✅ `yudao_system_user_dept_parse_function_not_found` — DeptParseFunction L41「获取部门为空」（35 调用/1460.0s, v1 PASS; Agent 探索用户模块接口，调用登录并调用 PUT /admin-api/system/user/update 修改用户信息，触发 mzt-biz-log 操作日志 DIFF 计算，比对已预置不存在部门的用户数据命中目标日志）
- ✅ `yudao_system_user_post_parse_function_not_found` — PostParseFunction L41「获取岗位为空」（35 调用/993.5s, v1 PASS; Agent 调用登录并调用 PUT /admin-api/system/user/update 修改用户信息，触发 mzt-biz-log 操作日志 DIFF 计算，比对已预置不存在岗位编号的用户数据命中目标日志）
- ⛔ `AdminUserParseFunction L41`「获取用户为空」— 静态/部署不可达。仅被 crm 模块（CRM_CLUE_TRANSFER 等）引用，crm 模块未部署于 system 服务。
- ⛔ `SocialClientApiImpl L90`「会员 openid 缺失」— 环境受限不可达。前置调用微信远程 API 获取订阅模板，无外部真实微信凭证环境下直接抛异常阻断。
- ⛔ `TenantServiceImpl L205/212`「updateTenantRoleMenu」— 框架多租户隔离缺陷阻断。updateTenantPackage 触发多租户遍历时，roleService.getRoleList() 未按租户过滤，在 L198 触发断言「角色租户不匹配」IllegalArgumentException，执行路径在到达日志前直接异常退出。

### small-swarm（9215, trace-real-mall-swarm-mall-demo）
- ✅ `small_swarm_brand_create` — DemoController L45（18 调用/322.7s）
- ✅ `small_swarm_brand_update` — DemoController L61（8 调用/111.1s）
- ✅ `small_swarm_brand_update_failed` — DemoController L64（12 调用/185.3s, v1 PASS; 传入不存在的品牌 id 调用 POST /brand/update/{id} 触发 count=0 分支命中目标日志）
- ✅ `small_swarm_brand_delete` — DemoController L75（13 调用/668.7s）
- ✅ `small_swarm_brand_delete_failed` — DemoController L78（16 调用/466.2s）
- ⛔ `DemoController L48 createBrand failed` — 静态不可达死代码。MyBatis insertSelective 成功返回 1，失败直接抛异常阻断，count 永不为 0。
- ⛔ `EsProductServiceImpl L203 recommend` — 需 Elasticsearch，未部署

### pig（9999, /tmp/pigboot.log）
- ✅ pig_file_not_exist / pig_change_password_wrong / pig_sms_unregistered
- ✅ pig_sms_login_not_exist（78 调用/455s）/ pig_opaque_token_locked（100 调用/558s）
- ✅ `pig_apikey_token_locked` — PigCustomOpaqueTokenIntrospector L163（64 调用/827.4s, v4 PASS; 经历 v1 预算超限、v2 残留缓存阻断、v3 密码试错，最终 v4 完成全自主编排）
- ⛔ `PigCustomOpaqueTokenIntrospector L100「用户账号 {} 已过期」` — 静态不可达死代码。PigUserDetailsService L99 在构造 PigUser 时将 accountNonExpired 参数硬编码为 true，导致 userDetails.isAccountNonExpired() 恒为真，isExpired 永不成立。

### ruoyi-plus（9201/9205）
- ✅ 10 个概念 sink 家族全部验证（menu 同级/根/路由名、mybatis 重复、wf 定义在用/同步/变量/孤儿/监听器）

### lamp-cloud（9216, trace-real-lamp-cloud-lamp-oauth-server）
- ✅ `lamp_oauth_login_success` — AbstractTokenGranter L425「用户：{} {} 登录成功」（35 调用/512.8s, v2 PASS; 通过 trace-agent 注入 SpringUtils.applicationContext 并挂载 ip2region_v4.xdb 修复登录后事件监听器 NPE 与 Bean 初始化失败，Agent 自主发送 POST /anyTenant/login 返回 HTTP 200 真实命中目标日志）
- ✅ `lamp_oauth_password_error_locked` — PasswordTokenGranter L122「密码最后一次输错后，解锁时间:」（35 调用/640.0s, v2 PASS; 配置 log_match 前缀排除 marker 误判，Agent 自主发送 POST /anyTenant/login 真实命中目标日志 3 次）
- ✅ `lamp_oauth_refresh_token` — RefreshTokenGranter L58「token={},obj={}」（35 调用/1260.0s, v1 PASS; Agent 自主调用 POST /anyTenant/refresh 传入带标记的 refreshToken 参数，真实命中目标日志 3 次）
- ✅ `lamp_oauth_user_status` — AbstractTokenGranter L284「userStatus=」（35 调用/723.2s, v1 PASS; 用户登录成功时加载员工信息并输出用户状态与员工ID，配置 log_match 前缀精确匹配，Agent 自主调用 POST /anyTenant/login 命中目标日志 4 次）
- ✅ `lamp_oauth_password_error_num` — PasswordTokenGranter L114「输错密码次数：」（26 调用/895.1s, v2 PASS; 预置密码错误次数达 10 次上限状态，Agent 自主调用 POST /anyTenant/login 触发锁定分支命中目标日志 3 次）
- ✅ `lamp_oauth_visible_router_default` — ResourceBiz L205「level={}, label={}」（26 调用/314.9s, v1 PASS; 开启 `--logging.level.top.tangyh=debug`，查询用户可见路由树 GET /anyone/visible/resource 携带 employeeId 与 applicationId，租户管理员遍历路由节点命中目标 debug 日志）
- ✅ `lamp_oauth_visible_router_vben5` — ResourceBiz L256「level={}, label={}」（22 调用/230.8s, v1 PASS; 查询 VBen5 格式可见路由树 GET /anyone/visible/resource?type=LAMP_WEB_PRO_VBEN5，遍历构建树结构命中目标 debug 日志）
- ✅ `lamp_oauth_visible_router_soybean` — ResourceBiz L336「level={}, label={}」（13 调用/220.7s, v1 PASS; 查询 Soybean 格式可见路由树 GET /anyone/visible/resource?type=LAMP_WEB_PRO_SOYBEAN，遍历构建树结构命中目标 debug 日志）
- ✅ `lamp_oauth_base_role_resource_set` — BaseRoleManagerImpl L91「resourceIdSet={}」（14 调用/430.0s, v1 PASS; 普通员工角色查询 GET /anyone/findVisibleResource，通过非管理员分支加载角色关联资源集合命中目标 debug 日志）

### apollo（8080, trace-real-apollo-apollo-adminservice）
- ✅ `apollo_delete_app` — AdminService L68「is deleting App:」（18 调用/180.6s, v1 PASS; Agent 自主调用 POST /apps 创建应用，随后 DELETE /apps 携带标记触发命中）
- ✅ `apollo_delete_app_namespace` — AppNamespaceService L188「is deleting AppNamespace, appId:」（9 调用/83.5s, v1 PASS; Agent 自主查库定位已有 AppNamespace，调用 DELETE /apps/{appId}/appnamespaces/{namespaceName} 携带 operator 查询参数与标记成功命中目标日志）
- ✅ `apollo_admin_invalid_access_token` — AdminServiceAuthenticationFilter L66「Invalid access token:」（8 调用/58.6s, v1 PASS; ServerConfig 启用 admin-service.access.control.enabled，Agent 自主向 /apps 发送携带非法 Authorization 请求头的请求，成功命中目标告警日志）
- ✅ `apollo_app_access_key_auto_provision_failed` — AppController L79「Failed to auto-provision access key for appId=」（25 调用/403.1s, v3 PASS; 预置 appId='seeded-limit-app' 达 5 条 AccessKey 上限，Agent 发送 POST /apps 创建该应用时触发密钥数量上限异常并被捕获，成功命中目标告警日志）
- ✅ `apollo_biz_config_invalid_override_value` — BizConfig L315「Invalid override config value:」（7 调用/158.0s, v3 PASS; ServerConfig 预置非法 JSON 格式的 appid.value.length.limit.override，Agent 调用 POST /apps/flagprobe-app/clusters/default/namespaces/application/items 创建配置项触发长度校验与 override 配置解析异常，成功命中目标错误日志）

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

