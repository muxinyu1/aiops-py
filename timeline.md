# 两周实验收官排期（2026-10-08 ～ 2026-10-21）

> 目标：清完 db_state 池剩余 ~220 个 sink（Yudao 114 + 其他项目 ~106），产出完整实验报告数据。
>
> 吞吐测算：单 sink 平均 ~7 分钟 Agent 运行（含 1.3 次重试系数）+ 环境预置，日均 2-3 小时运行量可覆盖 ~20 个/天，总量可行。瓶颈在**服务部署**与**不可达预筛**，不在跑数。

## 当前进度基线（2026-10-13 更新）

| 项目 | db_state 池 | 已验证 | 状态 |
|------|------------|--------|------|
| yudao | 117 | 6 PASS（pay 3 + mall 3）+ 5 静态不可达（pay 域） | trade/pay/system 已部署；**system 阻塞**（WebFrameworkUtils NPE，见 AGENTS.md §5.2） |
| paascloud | 37 | 0 | 未部署（无 Dockerfile） |
| lamp-cloud | 15 | 1 PASS（AbstractTokenGranter L425 登录成功） | oauth-server(9216) mapper-locations 修复并验证通过 |
| pig | 15 | 6（file_not_exist / change_password / sms 两批 / apikey_token_locked） | 已部署 |
| apollo | 11 | 0 | 未部署 |
| ruoyi-plus | 10 | 10（含 workflow 6 条） | 已部署 |
| small-swarm | 7 | 4（brand create/update/delete/delete_failed） | mall-demo 已部署(9215)；recommend 需 ES |
| 其他散量 | 13 | 0 | — |
| **合计** | **225** | **25** | |

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
- ✅ `yudao_pay_demo_withdraw_duplicate_notify` — PayDemoWithdrawServiceImpl L143（40 调用/408.6s）
- ⛔ "找不到支付客户端"家族 ×5 — L75/L97/L119/L227/L246 静态不可达（validPayChannel 先抛异常）

### yudao mall（9212, trace-real-yudao-cloud-trade-server）
- ✅ `yudao_mall_brokerage_withdraw_duplicate_notify` — BrokerageWithdrawServiceImpl L248（33 调用/960.2s）
- ✅ `yudao_mall_trade_order_paid_duplicate_notify` — TradeOrderUpdateServiceImpl L292（60 调用/1740.8s）
- ✅ `yudao_mall_trade_order_mismatch` — TradeOrderUpdateServiceImpl L295（35 调用/373.3s）

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

### apollo（8080, trace-real-apollo-apollo-adminservice）
- ✅ `apollo_delete_app` — AdminService L68「is deleting App:」（18 调用/180.6s, v1 PASS; Agent 自主调用 POST /apps 创建应用，随后 DELETE /apps 携带标记触发命中）

### mogu-blog（8080, trace-real-mogublog-mogu-admin）
- ✅ `mogu_admin_login_password_error` — LoginRestApi L120「管理员密码错误」（28 调用/775.6s, v2 PASS; v1 经历预算超限探索，v2 自主定位管理员账号并传入错误密码与攻击标记触发命中）

### novel-cloud（8080, trace-real-novel-cloud-novel-book-service）
- ✅ `novel_jwt_parse_failed` — JwtUtils L72「JWT解析失败:」（40 调用/995.4s, v1 PASS; 携带 Authorization 头部请求受拦截接口触发 JWT 校验失败分支）

### SpringBlade（8080/8106, trace-real-springblade-blade-auth）
- ⛔ `PasswordTokenGranter L82 / CaptchaTokenGranter L96`「用户登录失败」— 静态/动态不可达死代码。BladeBeanSerializerModifier 机制将下游 blade-system 返回的 null 对象序列化为 `{}`，经 Feign 反序列化为非空空对象 `new User()`，导致 `userInfo.getUser() == null` 永远不成立；下游故障时亦直接抛 500 异常跳过 fallback。

### RuoYi-Cloud（8080, trace-real-ruoyi-cloud-ruoyi-auth）
- ✅ `ruoyi_auth_token_parse_failed` — TokenService L113「获取用户信息异常」（15 调用/681.8s, v1 PASS; HeaderInterceptor 拦截携带非法 JWT 的 Authorization 请求头，解析抛异常进入 catch 命中目标日志）

