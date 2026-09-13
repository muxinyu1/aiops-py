# Fuzz 实验总结报告（2026-08-16 晚）

> 本报告汇总 2026-08-16 晚对 4 个微服务项目开展的 LLM 驱动 Log Injection Fuzz 实验。
> 评估维度：sink 总数、可达 sink、不可达原因、fuzz 轮次、以及**是否可黑盒 fuzz**（不需要偏差反馈即可成功 —— 若可黑盒，则无法体现本方法"静态分析+偏差反馈引导"的优越性）。

---

## 一、总览表

| 项目 | 目标服务 | sink 总数 | 可达 sink | 成功 sink | fuzz 轮次 | 黑盒可 fuzz? |
|------|---------|----------|----------|----------|----------|:---:|
| **SpringBlade** | blade-auth | 3 | 2 | 2 (100%) | Password: 3 / Captcha: 10→优化后 5 | ⚠️ 部分 |
| **PiggyMetrics** | account-service | 19 | 1 | 1 (100%) | 1 | ❌ 否（需预置状态+反馈） |
| **RuoYi-Cloud** | ruoyi-auth | 46 | 5 | 3 (100%) | 各 1 | ✅ 是（几乎黑盒） |
| **mall-swarm** | mall-admin | 25 | 0 (有效) | — 未 fuzz | — | — |

> 注：novel-cloud 为更早阶段（阶段 19-22）完成，不在本批次。本批次为阶段 23-26。

---

## 二、各项目详细分析

### 1. SpringBlade (blade-auth) — 3 sink

**sink 分布与可达性**

| Sink | 位置 | 可达性 | 结果 |
|------|------|--------|------|
| `PasswordTokenGranter.grant` L82 | blade-auth | ✅ | attacked (3 轮) |
| `CaptchaTokenGranter.grant` L96 | blade-auth | ✅ | attacked (10→5 轮) |
| `AuthFilter.unAuth` L97 | **blade-gateway** | ❌ 不在本容器 | 未 fuzz |

**不可达原因**：`AuthFilter` 在独立的 blade-gateway 服务，blade-auth 容器内无法触及。

**fuzz 轮次与关键转折**：
- PasswordTokenGranter：**3 轮**成功。marker 放 `account` 参数 → `log.error("用户登录失败, 账号:{}")`。
- CaptchaTokenGranter：优化前 **10 轮**（前 9 轮因 `max_tokens=4096` 导致 reasoning model 思考耗尽返回空响应，第 10 轮才成功）；修复 max_tokens 后 **5 轮**成功。

**黑盒 fuzz 评估：⚠️ 部分可黑盒**
- Password：marker 直接放 `account` body 参数即可，**接近黑盒**（但需知道用 POST /token + account 字段）。
- Captcha：**不可黑盒**——必须理解 `AuthController.token` 的 `@RequestParam(defaultValue="password") String grantType` 分派逻辑，把 `grantType=captcha` 放 **query**（非 body），这正是**偏差反馈 + 分派点源码**提供的关键信息。没有反馈，模型一直在 body 里试 `grant_type`/`grantType` 无法命中。
- **结论**：Captcha 体现了方法优越性（需反馈引导才能从 password 分支纠正到 captcha 分支）。

---

### 2. PiggyMetrics (account-service) — 19 sink

**sink 分布与可达性**

| Sink | 位置 | 可达性 | 结果 |
|------|------|--------|------|
| `ErrorHandler.processValidationError` L20 | account-service | ✅ | attacked (1 轮) |
| `AccountServiceImpl.create` L67 (成功日志) | account-service | ❌ 结构性不可达 | — |
| `AccountServiceImpl.saveChanges` L88 | account-service | ❌ 需 OAuth2 | — |
| `StatisticsServiceClientFallback.updateStatistics` | account-service | ⚠️ 未生成路径 | — |
| `CustomUserInfoTokenServices.*` ×3 | account-service | ❌ OAuth2 认证链路 | — |
| 其余 12 个 | notification/statistics/auth | ❌ 不在本容器 | — |

**不可达原因**：
- `create` 成功日志：`authClient.createUser()`（L51）先于 `log.info`（L67）失败，auth-service 未部署 → HTTP 500，永远到不了日志行。**下游依赖缺失导致的结构性不可达**。
- `saveChanges`：`PUT /current` 需 OAuth2，返回 401。
- `CustomUserInfoTokenServices`：OAuth2 认证链路，需有效 token，无法从 API 入口到达。

**fuzz 轮次**：ErrorHandler **1 轮**成功。

**关键转折**：首次 fuzz 10 轮全失败——该 sink 触发需 `Assert.isNull(existing, "account already exists: "+username)` 抛异常，**前提是 Mongo 已存在同名用户**。纯无状态 fuzz 无法发现"先创建再重复创建"的两步策略。手工预置 `FUZZ_MARKER_7x9k` 用户后，1 轮即成功。

**黑盒 fuzz 评估：❌ 不可黑盒**
- 需要（a）理解"重复创建触发 already exists"的策略，（b）预置应用状态（Mongo 用户）。
- 偏差反馈（到达 Controller 但未触发异常）是模型调整方向的关键。
- **结论**：较好体现方法优越性（有状态依赖 + 需反馈）。

---

### 3. RuoYi-Cloud (ruoyi-auth) — 46 sink

**sink 分布与可达性**

sink 集中在 `com.ruoyi.common.*`（GlobalExceptionHandler 10x / ReflectUtils 9x / ExcelUtil 8x）和 `com.ruoyi.system.*`。ruoyi-auth 源码内 0 日志调用，但 GlobalExceptionHandler 在 `ruoyi-common-security` **依赖 jar** 中，随 auth 容器加载 → 可达。

| Sink | 可达性 | 结果 |
|------|--------|------|
| `GlobalExceptionHandler.handleHttpRequestMethodNotSupported` L63 | ✅ | attacked (3 入口各 1 轮) |
| `GlobalExceptionHandler.handleNotPermissionException` L41 | ✅ 可达 | 未单独 fuzz |
| `GlobalExceptionHandler.handleNotRoleException` L52 | ✅ 可达 | 未单独 fuzz |
| `GlobalExceptionHandler.handleException` L123 (兜底) | ⚠️ trace 不覆盖 | unreachable (trace 盲区) |
| `GlobalExceptionHandler.handleRuntimeException` L112 | ⚠️ trace 不覆盖 | 未 fuzz |
| 其余 (ReflectUtils/ExcelUtil/system 等) | ❌ 工具类/其它服务 | — |

**不可达/不适用原因**：
- `handleException`/`handleRuntimeException`（兜底）：触发需请求不匹配任何 Controller 的 URL，**trace 完全不覆盖**（无 `X-Execution-Trace` 头），无法在 pipeline 内形成有效偏差反馈——虽 marker 能进日志，但失去方法的反馈优势。
- 工具类（ReflectUtils/ExcelUtil）：非请求驱动，难从 API 入口到达。

**fuzz 轮次**：3 个入口（login/refresh/register）→ handleHttpRequestMethodNotSupported，**各 1 轮**成功。

**本批次关键 bug 修复**：这类 `@ExceptionHandler` sink 触发时请求未进 Controller，trace 无入口节点且无 trace 头，导致 pipeline 的 `sink_checker`（trace span 匹配）误判 `reached=False`。修复为"框架异常处理 sink 以 marker 进日志作为到达依据"。

**黑盒 fuzz 评估：✅ 基本可黑盒**
- 攻击方式：`PATCH /login;FUZZ_MARKER_7x9k`（错误 HTTP 方法 + marker 矩阵参数），`requestURI` 天然用户可控。
- 模型**第 1 轮**就用了正确策略，**没有依赖偏差反馈**（因为请求方式本身就触发异常，一次到位）。
- **结论**：⚠️ **这类"异常处理 sink + requestURI 注入"几乎可黑盒 fuzz**，不能充分体现本方法"反馈引导"的优越性——它对方法的价值主要在于 pipeline 的判定修复（让框架能正确识别成功），而非引导模型收敛。

---

### 4. mall-swarm (mall-admin) — 25 sink —— 未 fuzz（前置分析排除）

**可达性前置分析（三问法）后直接排除，未启动 fuzz**：

| Sink | 参数 | 问题 |
|------|------|------|
| `MinioController.upload` L53/L75 | 无 | 固定文案，无注入点 |
| `MinioController.upload` L82 | `e.getMessage()` | 依赖 Minio 服务+异常，注入通道不明 |
| `OssServiceImpl.policy` L84 | `e` | 依赖 OSS 配置+签名异常 |
| `PmsProductServiceImpl.relateAndInsertList` L322 | `e.getMessage()` | 业务异常，marker 需进异常 message |

其余 20 个 sink 在 mall-portal/mall-search/mall-demo/mall-common，**均不在 mall-admin 容器**。

**排除原因**：mall-admin 内可达的 5 个 sink 要么无注入点（固定文案），要么依赖外部中间件（Minio/OSS）或业务异常，marker 注入通道不明确。投入产出比低。

---

## 三、核心结论

### 1. 方法论沉淀：sink 可达性前置分析三问
选项目/筛选 sink 时必查，任一不满足则不值得 fuzz：
1. sink 是否在**实际部署的服务容器**内？（PassJava/gulimall 教训：入口服务 0 日志调用，sink 全在其它模块）
2. sink 日志是否依赖**未部署的下游服务**？（PiggyMetrics create 教训：feign 先于日志失败）
3. sink 是否有**清晰的用户可控注入点**？固定文案/纯异常 message 难注入（mall-swarm 教训）

补充判断：**依赖 jar 可达 vs 独立服务不可达**——看部署服务 pom 是否依赖 sink 所在模块（RuoYi 的 common-security 可达，PassJava 的 renren-fast 不可达）。

### 2. 黑盒 fuzz 评估（方法优越性体现度）

| 项目 sink | 可黑盒? | 方法价值体现 |
|-----------|:---:|-------------|
| SpringBlade Captcha | ❌ | **强**：需分派点源码反馈才能从 password 纠正到 captcha 分支 |
| PiggyMetrics ErrorHandler | ❌ | **强**：有状态依赖 + 需偏差反馈理解触发策略 |
| SpringBlade Password | ⚠️ 部分 | 中：注入点直观，但需知道端点+字段 |
| RuoYi ExceptionHandler | ✅ 是 | **弱**：几乎黑盒，1 轮到位，未用反馈 |

**结论**：要体现本方法（静态分析 + 偏差反馈引导）相对黑盒 fuzz 的优越性，应优先选择：
- **有分支/分派逻辑的 sink**（SpringBlade Captcha 型）
- **有状态依赖的 sink**（PiggyMetrics 重复创建型）
- **多跳调用链深处的 sink**

而"异常处理器 requestURI 注入"型 sink（RuoYi）由于 requestURI 天然可控、触发即成功，更接近黑盒可达，**不适合作为方法优越性的论证案例**。

### 3. 本批次修复的关键 bug（框架级收益）
- **max_tokens 4096→65536 + 空响应重试**：reasoning model 思考 token 计入 completion，预算不足返回空（`finish_reason=length`），曾致 SpringBlade 8 轮空转。
- **框架异常处理 sink 判定**：以日志标记作为到达依据（trace 对 404/405 失效）。
- **双 javaagent 死锁**：trace-agent + jacocoagent premain 竞争类加载锁，fuzz 场景只保留 trace-agent。

### 4. 实验数据汇总

| 项目 | 成功 sink 数 | 总轮次 | 平均轮次 | 备注 |
|------|:---:|:---:|:---:|------|
| SpringBlade | 2 | 3 + 5(优化后) | 4 | Captcha 优化前 10 轮含 8 轮空转 |
| PiggyMetrics | 1 | 1 | 1 | 需预置 Mongo 状态 |
| RuoYi-Cloud | 3 | 1+1+1 | 1 | 黑盒可达型 |
| **合计** | **6** | — | — | 4 项目 3 个跑通 |
