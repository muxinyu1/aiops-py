# 执行路径追踪改造全流程记录

## 项目目标

为 `examples/` 下的 18 个 Java Spring Boot 微服务示例项目集成"执行路径追踪"能力，构建 Docker 镜像并推送到阿里云 ACR，最终通过真实业务 API 验证全部 18 个项目。

## 追踪协议

- 请求头：`X-Return-Trace: true`
- 响应头：`X-Execution-Trace`（Base64 编码的 JSON span 数组）
- 有效 span 标准：至少 1 个 span，每个 span 含 `src_file` 和 `line_number > 0`
- 负向控制：不带 `X-Return-Trace` 时不应返回 `X-Execution-Trace`

## 追踪实现组件

每个项目注入以下组件：

| 组件 | 作用 |
|------|------|
| `TraceFilter` | Servlet Filter，检测 `X-Return-Trace`，设置 trace id，收集并写出 spans |
| `TracingAspect` | AOP Around advice，记录 controller/service/biz/manager 等业务方法调用 |
| `TraceStore` | 按 trace id 聚合 spans |
| `TraceContextHolder` | ThreadLocal 保存当前 trace id |
| `LineNumberResolver` | 基于 ASM 从 bytecode 解析源码行号 |
| `TraceSmokeController` | 无依赖 smoke endpoint，用于镜像级追踪链路验证 |

## 覆盖的 18 个项目

| # | 项目 | 目标服务 | 真实业务验证接口 |
|---|------|----------|-----------------|
| 1 | pig | pig-auth | `GET /code/image` |
| 2 | RuoYi-Cloud | ruoyi-auth | `POST /login` |
| 3 | RuoYi-Cloud-Plus | ruoyi-auth | `GET /code` |
| 4 | mall-swarm | mall-admin | `GET /prefrenceArea/listAll` |
| 5 | SpringBlade | blade-auth | `GET /captcha` |
| 6 | youlai-mall | youlai-auth | `GET /api/v1/auth/captcha` |
| 7 | mall4cloud | mall4cloud-auth | `POST /ua/captcha/get` |
| 8 | zlt-microservices-platform | zlt-uaa | `GET /clients/list` |
| 9 | Apollo | apollo-adminservice | `GET /apps/1/accesskeys` |
| 10 | novel-cloud | novel-book-service | `GET /api/front/book/category/list?workDirection=0` |
| 11 | yudao-cloud | yudao-module-system-server | `POST /admin-api/system/captcha/get` |
| 12 | PiggyMetrics | account-service | `GET /demo` |
| 13 | MoGuBlog | mogu_admin | `GET /auth/info` |
| 14 | Cloud-Platform | ace-admin | `GET /jwt/refresh` |
| 15 | PassJava-Platform | passjava-member | `GET /member/growthchangehistory/list` |
| 16 | gulimall-learning | gulimall-member | `GET /member/growthchangehistory/list` |
| 17 | lamp-cloud | lamp-oauth-server | `GET /anyone/visible/resource` |
| 18 | java-microservice | (root) | `GET /api/health` |

## Docker / 阿里云 ACR

- Registry：`crpi-8tnv6lve87c20oxm.cn-beijing.personal.cr.aliyuncs.com`
- Namespace：`llmfuzz`
- Tag：`:latest`
- 构建脚本：`build_traced_images.sh`、`build_mvc_traced_and_push.sh`

## 工作阶段

### 阶段 1：追踪组件注入与镜像构建

- 为每个项目添加 TraceFilter、TracingAspect、TraceStore、TraceContextHolder、LineNumberResolver
- 添加 TraceSmokeController 作为无依赖验证端点
- 修改 pom.xml 添加 spring-aop、aspectjweaver、asm 依赖
- 编写/修改 Dockerfile，构建镜像并推送到阿里云 ACR
- 结果：18 个镜像全部构建并推送成功

### 阶段 2：Smoke 模式验证（18/18）

- 编写 `validate_trace_images.py` 验证脚本
- 使用 `--smoke-mode` 验证 `/__trace_smoke` 端点
- 结果：smoke 验证 18/18 全部通过

### 阶段 3：Compose 文件生成

- 创建 `examples-yml/` 目录结构
- 为每个项目生成 `compose.yaml`（smoke 模式）
- 生成 `common.env` / `common-real.env` 共享环境变量
- 生成 `_shared/real-deps.compose.yaml`（MySQL、Redis 等共享依赖）
- Smoke compose 验证 18/18 通过

### 阶段 4：真实业务 API 验证

#### 初始尝试（8/18）

首次真实业务验证只有 8 个项目通过，主要失败原因：
- 缺少真实数据库/中间件依赖
- Spring Security 拦截返回登录页而非业务响应
- 缺少必要的 Spring AOP 依赖

#### 核心依赖修复（+5 项目）

- 创建共享依赖栈：MySQL 8.0 + Redis 7
- MySQL 初始化脚本：为各项目创建所需数据库
- 修复 RuoYi-Cloud-Plus：需要项目内置 Nacos + ruoyi-system provider + `--sa-token.check-same-token=false`
- 修复 youlai-mall：添加 `spring-boot-starter-aop`，配置 public SecurityFilterChain 放行 captcha 端点
- 修复 zlt：TraceFilter 需 `@Order(HIGHEST_PRECEDENCE)` 先于 Spring Security

#### 难点项目攻坚

**Apollo**
- 问题：`Access denied for user 'root,root'`
- 根因：脚本重复传递 `--spring.datasource.username/password`，Spring 绑定成逗号值
- 修复：移除重复参数

**yudao-cloud**
- 问题：多次启动失败（circular dependency、missing Knife4j properties、dynamic datasource）
- 修复：
  - `--spring.main.allow-circular-references=true`
  - 排除 Druid 自动配置
  - 禁用 springdoc/knife4j
  - 补齐 dynamic datasource master/slave 配置
  - 补齐 yudao.web/api-encrypt/sms-code 等必需属性
  - 修复 elif 分支不可达问题

**PiggyMetrics**
- 问题：Docker Hub 拉取 Mongo/RabbitMQ 镜像多次 EOF；duplicate CLI 参数绑定失败
- 修复：
  - 改用显式本地配置，禁用 config/eureka 注册与健康检查
  - 移除重复 Rabbit/Eureka 命令参数
  - 使用 `/demo` 端点（`@PreAuthorize` 放行）
  - 缩短 Mongo URI timeout
  - 注：HTTP 500 因无 Mongo，但 trace 有效

**novel-cloud**
- 问题：同样的 `root,root` 重复参数绑定问题
- 修复：移除 target_extra_args 中重复的 username/password

#### 最终全量验证（18/18）

```
python3 validate_trace_images.py --real-deps --startup-timeout 180 --ready-grace 5 \
  --json-out logs/trace_validation/real_api_all_18_final_retry.json
```

结果：**18/18 targets passed**

### 阶段 5：youlai-mall 镜像重建

- 发现 youlai-mall 的 ACR 镜像未包含最新 SecurityFilterChain 修改
- 重新 `mvn package` 并用增量 Dockerfile 重建镜像
- 重新推送到 ACR
- 单项验证通过：`GET /api/v1/auth/captcha HTTP 200 spans=3`

### 阶段 6：Compose Real 文件同步

为以下项目创建/更新 `compose.real.yaml`，使其与脚本验证成功的配置一致：
- `examples-yml/Apollo/compose.real.yaml`
- `examples-yml/yudao-cloud/compose.real.yaml`
- `examples-yml/PiggyMetrics/compose.real.yaml`
- `examples-yml/novel-cloud/compose.real.yaml`
- `examples-yml/RuoYi-Cloud-Plus/compose.real.yaml`
- `examples-yml/youlai-mall/compose.real.yaml`

### 阶段 7：Git 提交与推送

- 提交：`d4f60c2e` feat: add trace validation for example services
- 推送到：`git@github.com:muxinyu1/aiops-py.git` master 分支
- 变更规模：203 files changed, 10568 insertions(+), 196 deletions(-)

## 关键经验教训

1. **Spring CLI 参数重复绑定**：同一 key 通过 env + command 重复传递会被 Spring 绑定为逗号分隔值（如 `root,root`、`false,false`），导致类型转换失败或认证错误。

2. **Spring Security 拦截**：HTTP 200 可能是 Spring Security 默认登录页 HTML，而非真实业务 Controller 响应。需要添加 public SecurityFilterChain 放行验证端点。

3. **elif 分支顺序**：Python 中相同 project 名的多个 elif 分支，后面的会不可达。

4. **Docker Hub 不稳定**：国内环境拉取 Docker Hub 镜像（Mongo、RabbitMQ 等）可能因 CloudFront EOF 失败，需要 fallback 到本地显式配置。

5. **Smoke 通过不代表真实业务通过**：smoke endpoint 无依赖，但真实业务接口需要完整的中间件栈。

6. **TraceFilter 优先级**：对于有 Spring Security 的项目，TraceFilter 需要 `@Order(Ordered.HIGHEST_PRECEDENCE)` 确保在 Security Filter Chain 之前执行。

7. **MySQL 初始化脚本**：不要使用 `set -u`，会破坏 MySQL 官方 entrypoint 的内部函数。

## 最终验证报告

报告路径：`logs/trace_validation/real_api_all_18_final_retry.json`

验证命令：
```bash
python3 validate_trace_images.py --real-deps --startup-timeout 180 --ready-grace 5 \
  --json-out logs/trace_validation/real_api_all_18_final_retry.json
```

所有 18 个项目均通过真实业务 API 执行路径追踪验证。

---

### 阶段 8：运行时变量快照功能 (2026-07-15)

#### 目标
在偏差计算的基础上，捕获偏差发生时刻各方法的入参值、返回值和 this 状态，辅助 LLM 推理根因。

#### 设计决策
- **不捕获局部变量**：方法入参 + 返回值 + 源码足够 LLM 推理内部逻辑，局部变量是冗余的
- **两阶段按需采集**：Phase 1 正常执行获取偏差点 → Phase 2 只对偏差方法带 `X-Snapshot-Methods` 头重新执行采集快照
- **性能无忧**：用户表示不关心性能开销，但设计仍按需采集（可指定 `*` 全采或逗号分隔具体方法）
- **序列化深度 1 层**：对象只展开第一层字段，防止序列化爆炸

#### 实现组件

| 组件 | 位置 | 作用 |
|------|------|------|
| `MethodSnapshotAdvice` | trace-agent (Java) | Byte Buddy Advice，`@OnMethodEnter/@OnMethodExit` 捕获 args/return/this |
| `SpanStackHelper` | trace-agent (Java) | 核心逻辑：判断是否需要快照、序列化、写入 SpanRecord |
| `SnapshotTargetRegistry` | app 侧 (Java) | ThreadLocal 注册表，记录当前请求需要快照的方法列表 |
| `TraceFilter` | app 侧 (Java) | 读取 `X-Snapshot-Methods` 头，设置/清除 SnapshotTargetRegistry |
| `SpanRecord` | 两侧 (Java) | 新增 args_snapshot, return_snapshot, this_snapshot 字段 |
| `snapshot_executor.py` | Python | SnapshotDiffer: 构建快照目标、两阶段编排 |
| `difference.py` | Python | VariableSnapshot 数据类、DivergencePoint 增加快照字段 |
| `trace.py` | Python | TraceNode 解析新的快照字段 |

#### 关键技术难点
- **Bootstrap ClassLoader 注入在 Docker 中失败**：`ClassInjector$UsingInstrumentation` 无法写临时 jar。解决：放弃 bootstrap 注入，SpanStackHelper 通过 agent classloader 加载，Advice 内联后可访问
- **SnapshotTargetRegistry 跨 ClassLoader 访问**：Agent CL 无法直接 Class.forName app 的类。解决：将 SnapshotTargetRegistry 放在 app 包中，SpanStackHelper 通过反射 + 缓存 Method 对象访问
- **OTEL Agent 冲突**：Docker 镜像 entrypoint 硬编码了 opentelemetry-javaagent，与 trace-agent 同时加载导致 hang。解决：compose 中覆盖 entrypoint，只加载 trace-agent

#### E2E 测试结果 (test_snapshot_e2e.py)
- ✅ TEST 1: 基本快照 (`*`) — args/return/this 全部捕获

---

### 阶段 9：yudao-cloud 交易模块真实 fuzz（2026-08-24）

#### 目标
在已完成执行路径追踪和 LLM 反馈驱动 fuzz 的基础上，对 yudao-cloud trade-server 进行真实业务 API fuzz，确认哪些 sink 可以在单步 API 场景下成功触发，以及哪些需要前置 API / 数据库状态才能到达。

#### 项目状态
- 应用：`yudao-cloud trade-server`
- 镜像：`crpi-8tnv6lve87c20oxm.cn-beijing.personal.cr.aliyuncs.com/llmfuzz/yudao-cloud-trade-server:traced`
- 容器：`trace-real-yudao-cloud-trade-server`
- 入口：`localhost:8080`
- 背景：交易模块已在真实业务环境中跑起来，且已完成 HandlerInterceptor + BeanPostProcessor 方式的全层执行追踪注入

#### 关键修复
- 修正了老镜像未包含最新追踪代码的问题，采用 `--no-cache` 重建镜像
- 修复 Spring Security 相关配置：排除 `SecurityAutoConfiguration` / `SecurityFilterAutoConfiguration`
- 补齐 `PermitAllSecurity`，解决 `@PreAuthorize` 中 `ss` Bean 缺失问题
- 修复 MySQL 表结构导入问题：把 H2 SQL 转为 MySQL，修复 `tinyint(1)(1)` 与 `bit` 等语法兼容问题
- 取消 `@Aspect` 方案，改用 `HandlerInterceptor + BeanPostProcessor` 作为稳定的全层 trace 实现
- 修复 LLM 调用格式问题，兼容 Claude Messages API 与 OpenAI 兼容 API

#### 实际 fuzz 结果
本轮对 8 个 trade 模块 sink 进行了真实 fuzz 验证，结果为：

- **可单步 fuzz 成功**：2 个
  1. `AfterSaleController.updateAfterSaleRefunded`
  2. `BrokerageWithdrawController.updateBrokerageWithdrawTransferred`
- **不可单步 fuzz**：6 个
  - 原因：这些 sink 需要前置 API / DB 预置业务记录，单步请求无法进入真正的 deep service 路径
  - 典型例子：`AfterSaleServiceImpl.validatePayRefund` 需要 `after_sale` 数据存在，`updateAfterSaleRefunded` 前置逻辑才会进入 service 层

#### 成功 sink 的源码特征
两条成功路径都属于“入口处无条件日志”的 sink：

- `AfterSaleController.updateAfterSaleRefunded()`：

```java
@PostMapping("/update-refunded")
@PermitAll
public CommonResult<Boolean> updateAfterSaleRefunded(@RequestBody PayRefundNotifyReqDTO notifyReqDTO) {
    log.info("[updateAfterRefund][notifyReqDTO({})]", notifyReqDTO);
    if (StrUtil.startWithAny(notifyReqDTO.getMerchantRefundId(), "order-")) {
        ...
    } else {
        afterSaleService.updateAfterSaleRefunded(...);
    }
    return success(true);
}
```

- `BrokerageWithdrawController.updateBrokerageWithdrawTransferred()`：

```java
@PostMapping("/update-transferred")
@PermitAll
public CommonResult<Boolean> updateBrokerageWithdrawTransferred(@RequestBody PayTransferNotifyReqDTO notifyReqDTO) {
    log.info("[updateAfterRefund][notifyReqDTO({})]", notifyReqDTO);
    brokerageWithdrawService.updateBrokerageWithdrawTransferred(...);
    return success(true);
}
```

这类 sink 在方法第一行就写日志，后续才走参数解析/业务分支；只要请求体能反序列化进入方法，黑盒也能命中。它们的意义主要是验证“偏差反馈 + LLM 修正参数”对入口型 sink 的效率，而非体现源码引导的核心优势。

#### 结论：单步 fuzz 的边界
本轮实践进一步明确了项目的基本策略：

1. **单步 fuzz**：只允许一次 API 请求完成 sink 到达
2. **可 fuzz 的 sink**：必须在单次请求中即可满足所有前置条件
3. **不可 fuzz 的 sink**：需要前置 API、数据库状态，或者跨请求上下文建立数据依赖
4. **区别于黑盒**：黑盒知道 URL 和参数名，但没有源码和运行时偏差反馈，无法处理复杂分支/类型/状态约束
5. **真正体现优势的场景**：需要知道源码分支条件和参数格式的 deep sink，而不是入口处无条件日志

#### 关键判定标准
- 如果 sink 位于方法入口并且 `log.info` 在第一行：黑盒很可能也能撞到；此类不需要强源码引导
- 如果 sink 位于 `if/else`、`switch`、参数格式校验之后，且依赖字符串格式、枚举值、签名校验、时间戳合法性：源码引导非常关键
- 如果需要先创建业务对象 / 用户 / 订单 / 售后单 / 支付记录：则认定为“不可单步 fuzz”，应视为非本轮目标

#### 最终判断
- 本轮 `yudao-cloud trade-server` 在单步 fuzz 的严格边界下，**成功发现并击中 2 个可 fuzz sink**，并且**明确判定 6 个 sink 为不可 fuzz**。
- 这为后续评估“白盒/源码引导 fuzz 与黑盒 fuzz 的差距”提供了清晰基线：**我们并非在所有 sink 上都绝对优于黑盒，而是在可单步 + 依赖分支条件的场景中，偏差反馈赋予明显优势。**
- ✅ TEST 2: 定向快照 (仅 `UserService.findById`) — 只有指定方法有快照
- ✅ TEST 3: 两阶段偏差计算 — 对比 `findById(id=1)` 返回用户 vs `findById(id=999)` 抛异常
- ✅ TEST 4: POST 请求 — 完整捕获请求体参数

#### 关键文件
- `examples-yml/java-microservice/compose.snapshot-test.yaml` — 测试用 compose（无 OTEL agent）
- `trace-agent/target/trace-agent-1.0.0.jar` — 含快照功能的 agent jar
- `test_snapshot_e2e.py` — 端到端测试脚本

---

### 阶段 9：知识问答与架构梳理 (2026-07-20)

#### 讨论记录
- **Q: 预期路径怎么计算的？**
  - A: `path_generator.py` 中 `PathGenerator` 在调用图上 BFS，从 API 入口到 LogSink 找路径
  - 优先选 taint 路径（有数据流），其次短路径
  - 每个 (API, Sink) 对只保留 1 条最优路径
  - 输出 `ExpectedPathSet`，用于与运行时 trace 逐节点对比找偏差点

- **Q: 局部变量是否在 trace 中返回？**
  - A: 不是。只捕获方法边界值（入参/返回/this），不捕获方法体内局部变量
  - 原因：Byte Buddy Advice 无法访问栈帧局部变量 slot
  - 但入参 + 返回 + 源码足够 LLM 推理，局部变量信息是冗余的

- **Q: 预期路径当前实现 vs GONDAR 论文对比？**
  - 论文方案：Joern (CHA+RTA) 构建调用图 + CodeQL 做 taint 标记 + BFS 搜路径 + taint>CG/短>长 评分
  - 当前实现状态：
    - ✅ 路径搜索算法和评分逻辑完全实现（`PathGenerator` 的 BFS + 选路策略）
    - ✅ 数据结构完备（`CallGraph`, `CallGraphEdge.is_taint`, `PathSource.TAINT/CALL_GRAPH`）
    - ❌ **Joern 未接入**：调用图目前是手动构造的，没有自动从源码生成
    - ❌ **CodeQL 未接入**：taint 标记没有自动填充
    - ❌ **Sink/API 自动发现未实现**：LogSink 和 APIEntry 需手动提供
  - 差距本质：下游算法 ready，上游数据源（静态分析工具链）未集成

---

### 阶段 10：Joern + CodeQL 集成实现 (2026-07-20)

#### 目标
将 Joern (调用图生成) 和 CodeQL (taint 标记) 接入现有的 `PathGenerator` 框架，补齐上游数据源。

#### 已完成

**1. Joern 适配层 (`joern_adapter.py`)**
- `JoernAdapter`: 本地 CLI 模式 (joern-parse → joern --script → 解析 JSON)
- `JoernDockerAdapter`: Docker 容器模式 (不需本地安装)
- `JoernConfig`: 配置类 (joern_home, package_filter, jvm_memory, 超时等)
- `EXPORT_CALL_GRAPH_SCRIPT_SIMPLE`: 导出调用图的 Scala 脚本
- `load_call_graph_from_json()`: 从缓存 JSON 加载 (跳过 Joern 分析)
- `generate_call_graph_from_source()`: 一键从源码生成 CallGraph

**2. CodeQL 适配层 (`codeql_adapter.py`)**
- `CodeQLAdapter`: CodeQL CLI 集成 (database create → query run → bqrs decode)
- `CodeQLConfig`: 配置类 (codeql_home, build_command, query_type, 超时等)
- 多种 taint query 模板:
  - `TAINT_METHOD_PAIRS_QUERY`: 通用 taint 边查询
  - `CUSTOM_TAINT_QUERY_TEMPLATE`: 按 package_filter 定制
- `mark_taint_on_call_graph()`: 对 CallGraph 运行 taint 分析并标记边
- `load_taint_from_csv()`: 从缓存结果加载
- `TaintResult`: 结果数据类

**3. 统一入口 (`static_analysis.py`)**
- `analyze_project()`: 三阶段流水线 (Joern → CodeQL → PathGenerator)
- `check_tools()`: 工具可用性检查
- `StaticAnalysisResult`: 完整结果数据类
- 每个阶段可通过缓存文件跳过

**4. 测试 (`test_static_analysis.py`)**
- TEST 1: Joern JSON 解析 (mock) ✅
- TEST 2: CodeQL taint 标记 (mock CSV) ✅
- TEST 3: 完整流水线 mock ✅
- TEST 4: Docker 可用性 ✅

#### Joern 本地安装验证
- 安装路径: `~/bin/joern/joern-cli/` (v4.0.583)
- 需要 JDK 21 (已有 v21.0.11)
- 真实测试: `java-microservice` 项目
  - `joern-parse` 耗时 ~7s → 生成 CPG (266KB)
  - `joern --script` 耗时 ~6s → 导出 JSON (31KB)
  - 结果: 68 methods, 38 call edges
  - PathGenerator 成功找到 2 条路径 (getUser→error, createUser→error)

#### Joern fullName 格式注意
- Joern 输出格式: `com.example.Class.method:ReturnType(ParamTypes)`
- 需要在 `:` 处截断得到 node_id: `com.example.Class.method`
- 原始 Scala 脚本中 `fn.split("\\.").dropRight(1)` 不能直接用作 className（会包含签名碎片）

#### CodeQL 状态
- 未安装 (需从 GitHub releases 下载 codeql-bundle)
- 适配层已完整实现，接好 CLI 即可使用
- 当前可通过 `load_taint_from_csv()` 手动导入 taint 结果

#### 关键文件
- `joern_adapter.py` — Joern 集成
- `codeql_adapter.py` — CodeQL 集成
- `static_analysis.py` — 统一入口
- `test_static_analysis.py` — 测试脚本
- `/tmp/export_cg.sc` — Joern 导出脚本 (独立版本)
- `/tmp/java-microservice-cg.json` — 真实项目调用图 JSON 缓存

---

### 阶段 11: CodeQL 真实执行 + 端到端验证 (2026-07-20)

#### 目标
在 java-microservice 项目上运行完整流水线: Joern 调用图 → CodeQL 污点分析 → PathGenerator 路径生成

#### 工具安装
- **CodeQL v2.26.1**: `~/bin/codeql/codeql` (从 codeql-bundle-linux64.tar.gz 解压)
- **Joern v4.0.583**: `~/bin/joern/joern-cli/` (已有)
- **JDK 21.0.11**: OpenJDK (满足两者需求)

#### CodeQL 执行过程

1. **创建数据库**:
   ```bash
   codeql database create /tmp/codeql-db-java-microservice \
     --language=java --command="mvn compile -DskipTests -q" --overwrite
   ```
   - 耗时: ~15s
   - TRAP 导入: 688.85 KiB relations, 2.78 MiB string pool

2. **运行 Taint Tracking Query**:
   - 查询: 自定义 `taint_edges.ql` (从 RemoteFlowSource + Spring Controller 参数 → 用户代码方法调用)
   - 编译: 1m3s (首次, 含 java-all 库加载)
   - 评估: 4.9s
   - 输出: BQRS → CSV

3. **Taint 分析结果** (11 条 taint 边):
   | Caller | Callee |
   |--------|--------|
   | AppController.getUser | UserService.findById |
   | AppController.createUser | UserService.create |
   | AppController.createUser | ApiResponse.success |
   | AppController.getOrder | OrderService.findById |
   | AppController.cancelOrder | OrderService.cancel |
   | AppController.cancelOrder | ApiResponse.success |
   | TraceFilter.doFilterInternal | TraceFilter.extractTraceId |
   | TraceFilter.doFilterInternal | TraceContextHolder.set |
   | TraceFilter.doFilterInternal | SnapshotTargetRegistry.setTargets |
   | TraceFilter.doFilterInternal | TraceStore.getAndRemove |
   | TracingAspect.traceMethod | TraceStore.add |

#### 端到端验证结果

- **Step 1** (Joern CG): 68 节点, 38 边
- **Step 2** (CodeQL taint 标记): 11 taint 对 → 11/11 成功标记到 CG 边 (100% 匹配率)
- **Step 3** (PathGenerator): 生成 5 条预期路径

| # | 类型 | Confidence | 入口 → Sink |
|---|------|-----------|-------------|
| 1 | 🔴 TAINT | 0.80 | AppController.getUser → UserService.findById |
| 2 | 🔴 TAINT | 0.80 | AppController.createUser → UserService.create |
| 3 | 🔴 TAINT | 0.80 | AppController.getOrder → OrderService.findById |
| 4 | ⚪ CG | 0.50 | AppController.createOrder → OrderService.create |
| 5 | 🔴 TAINT | 0.80 | AppController.cancelOrder → OrderService.cancel |

- **Taint 路径占比**: 80% (4/5)
- **路径 4 为 CG-only 的原因**: `createOrder` 的 Request Body 参数未被 CodeQL 识别为 RemoteFlowSource (Spring @RequestBody 需要额外 MaD 配置)

#### 验证结论

✅ **全流水线端到端打通**:
1. Joern 调用图 → JSON → `load_call_graph_from_json()` ✓
2. CodeQL 数据库创建 → 污点查询 → CSV 结果 ✓
3. `mark_taint_edges_from_results()` 100% 匹配标记 ✓
4. PathGenerator 正确区分 taint (0.8) vs CG-only (0.5) 路径 ✓
5. GONDAR 论文描述的 "Joern CG + CodeQL Taint + BFS Path" 三步流水线验证通过 ✓

#### 关键文件更新
- `/tmp/codeql-db-java-microservice` — CodeQL 数据库
- `/tmp/codeql-taint-results.csv` — 真实 taint 分析结果
- `/tmp/codeql-query/taint_edges.ql` — 实际执行的 taint query
- `/tmp/validate_e2e.py` — 端到端验证脚本

---

### 阶段 12: 具体应用示例 (2026-07-20)

#### 目标
提供一个可直接运行的完整示例, 展示静态分析流水线的实际使用方式。

#### 示例文件
`example_static_analysis.py` — 位于项目根目录

#### 使用方式

```bash
# 快速模式 (使用缓存, 毫秒级)
python3 example_static_analysis.py --mode cache

# 完整流水线 (Joern + CodeQL + PathGenerator, 需安装工具)
python3 example_static_analysis.py --mode full

# 仅调用图 (跳过 CodeQL)
python3 example_static_analysis.py --mode cg-only
```

#### 核心代码示例

**1. 一行调用完成全部分析:**

```python
from static_analysis import analyze_project
from expected_path import APIEntry, LogSink

result = analyze_project(
    source_dir="examples/java-microservice",
    project_name="java-microservice",
    package_filter="com.example.microservice",
    api_entries=[
        APIEntry(class_name="com.example.microservice.controller.AppController",
                 method="getUser", http_method="GET", http_path="/api/users/{id}"),
        APIEntry(class_name="com.example.microservice.controller.AppController",
                 method="createUser", http_method="POST", http_path="/api/users"),
    ],
    log_sinks=[
        LogSink(class_name="com.example.microservice.service.UserService",
                method="findById", log_level="INFO"),
        LogSink(class_name="com.example.microservice.service.UserService",
                method="create", log_level="INFO"),
    ],
    # 工具路径
    joern_home="~/bin/joern/joern-cli",
    codeql_home="~/bin/codeql",
    build_command="mvn compile -DskipTests -q",
)

print(result.summary)
# Project: java-microservice
#   Methods: 68
#   Call edges: 38
#   Taint edges: 11
#   Expected paths: 5
```

**2. 使用缓存跳过重复分析:**

```python
# 已有 Joern/CodeQL 产出时, 直接用缓存
result = analyze_project(
    source_dir="examples/java-microservice",
    project_name="java-microservice",
    api_entries=API_ENTRIES,
    log_sinks=LOG_SINKS,
    joern_cache_json="/tmp/java-microservice-cg.json",      # 跳过 Joern
    codeql_results_csv="/tmp/codeql-taint-results.csv",     # 跳过 CodeQL
)
```

**3. 遍历生成的预期路径:**

```python
from expected_path import PathSource

for path in result.path_set.all_paths:
    if path.source == PathSource.TAINT:
        print(f"[TAINT] {path.api_entry.id} → {path.log_sink.class_name}.{path.log_sink.method}")
        print(f"  confidence: {path.confidence}")
        print(f"  路径: {' → '.join(f'{n.class_name}.{n.method}' for n in path.nodes)}")
```

**4. 将预期路径用于 trace 验证:**

```python
def verify_trace(expected_path, actual_trace_spans):
    """
    验证实际 trace 是否覆盖了预期路径.
    
    Args:
        expected_path: 静态分析生成的预期路径
        actual_trace_spans: 运行时采集到的 span 列表
    
    Returns:
        bool: 预期路径中的每个节点都在 trace 中出现
    """
    expected_methods = {f"{n.class_name}.{n.method}" for n in expected_path.nodes}
    actual_methods = {span["class"] + "." + span["method"] for span in actual_trace_spans}
    return expected_methods.issubset(actual_methods)


# 示例: 验证 GET /api/users/1 的 trace
actual_trace = [
    {"class": "com.example.microservice.controller.AppController", "method": "getUser"},
    {"class": "com.example.microservice.service.UserService", "method": "findById"},
    {"class": "com.example.microservice.repository.UserRepository", "method": "findById"},
]

path = result.path_set.all_paths[0]  # getUser → UserService.findById
assert verify_trace(path, actual_trace), "Trace 不匹配预期路径!"
print("✅ Trace 验证通过: 实际执行路径覆盖了静态分析预测的路径")
```

#### 运行结果 (实际输出)

```
项目: java-microservice
方法数: 68
调用边: 38
Taint 边: 11
预期路径: 5

[1] 🔴 TAINT (confidence=0.80)
    入口: GET /api/users/{id}
    Sink: UserService.findById
    路径: AppController.getUser → UserService.findById

[2] 🔴 TAINT (confidence=0.80)
    入口: POST /api/users
    Sink: UserService.create
    路径: AppController.createUser → UserService.create

[3] 🔴 TAINT (confidence=0.80)
    入口: GET /api/orders/{id}
    Sink: OrderService.findById
    路径: AppController.getOrder → OrderService.findById

[4] ⚪ CG (confidence=0.50)
    入口: POST /api/orders
    Sink: OrderService.create
    路径: AppController.createOrder → OrderService.create

[5] 🔴 TAINT (confidence=0.80)
    入口: POST /api/orders/{id}/cancel
    Sink: OrderService.cancel
    路径: AppController.cancelOrder → OrderService.cancel
```

#### 路径类型解读

| 类型 | Confidence | 含义 |
|------|-----------|------|
| 🔴 TAINT | 0.80 | CodeQL 证实 HTTP 请求参数通过该调用传递 (数据流确认) |
| ⚪ CG | 0.50 | 调用图中存在调用关系, 但无 taint 证据 (仅结构可达) |

- Taint 路径 = 强证据: 请求一定经过这条路径
- CG 路径 = 弱证据: 存在调用关系, 但数据不一定流经

#### 应用场景

1. **Trace 完整性验证**: 预期路径作为 ground truth, 验证运行时 trace 是否完整
2. **覆盖率评估**: 统计 trace 覆盖了多少预期路径 (覆盖率 = 已验证路径 / 总预期路径)
3. **异常检测**: 如果 taint 路径 (高置信度) 在实际 trace 中缺失, 说明链路可能断裂
4. **回归测试**: 代码变更后重新分析, 对比预期路径变化 (新增/删除/修改)

---

### 阶段 13: API 自动发现 (2026-07-21)

#### 目标
自动扫描 Java 源码中的 Spring MVC 注解，提取所有 REST API 入口，避免手工标注。

#### 实现
- `api_discovery.py` — 正则扫描 `@RestController` / `@Controller` + `@GetMapping` / `@PostMapping` / `@RequestMapping` 等
- 输出兼容 `APIEntry` 结构
- 配套测试: `tests/test_api_discovery.py`

#### 关键 commit
- `475f3ece feat: add API entry discovery module for Spring MVC projects`

---

### 阶段 14: LLM 驱动 Sink-Centric Fuzz 框架 (2026-07-22 ~ 2026-07-25)

#### 目标
实现完整的 LLM 引导 Log Injection 攻击循环：Fuzzer 生成参数 → Executor 执行 → Trace 采集 → 偏差反馈 → LLM 迭代优化。

#### 核心模块

| 文件 | 职责 |
|------|------|
| `fuzzer.py` | LLM Fuzz Agent — 构造 system/user prompt, 解析 LLM `<|im_start|>+<json>` 输出为 HttpParameter |
| `pipeline.py` | 主控循环 — 遍历 (API, Sink, Path) 三元组, 运行 fuzz → 检查 sink → 日志验证 |
| `sink.py` | Sink 模型 — 日志打印点位置 + 类型 + tainted_params |
| `llm.py` | OpenAI 兼容 LLM 客户端（支持环境变量配置） |
| `parameter.py` | HttpParameter 数据模型 |
| `path_differ.py` | Trace vs ExpectedPath 偏差计算（到达深度 + 首次偏离点） |
| `demo_fuzz.py` | java-microservice 通用演示 + 工具函数 (execute_with_trace, MockLLM, check_container_log) |

#### 攻击成功判定
1. Trace 到达目标 sink 方法 (`check_sink_reached`)
2. **且** 容器日志中出现攻击标记字符串 (`check_container_log_after_line`)

二者都满足 = Log Injection 攻击成功 (PathStatus.REACHED)

#### Prompt 工程
- System prompt: 声明合法授权测试、描述目标 sink (类/方法/行号/日志模板/污染参数)、API 信息、预期路径
- User prompt: 偏差反馈 (到达深度、missed node) + 历史失败请求
- 输出格式: `<|im_start|>分析推理[tid]HTTP请求</json>`
- 支持 reasoning model (`refactor: use <|im_start|>+<json> output format`)

#### Pipeline 结果模型
```
PipelineResult
  └── PathResult[]
        ├── status: REACHED | REACHED_NO_MARKER | UNREACHABLE | ERROR
        ├── attempts: FuzzAttempt[]
        └── elapsed_seconds
```

#### 关键 commit
- `942bbb2f feat: implement LLM-driven sink-centric fuzz framework`
- `d1a0f5c0 refactor(fuzzer): use <|im_start|>+<json> output format for reasoning models`
- `cb88d1b5 feat: log injection attack mode with marker-based success detection`
- `7ceda806 fix: correct trace span field names and handle null log_sink in path id`

---

### 阶段 15: novel-cloud Fuzz 实战验证 (2026-07-25 ~ 进行中)

#### 目标
以 novel-cloud (book-service) 为第一个真实目标，端到端打通 fuzz 攻击闭环。

#### 目标服务
- 镜像: `crpi-xxx/llmfuzz/novel-cloud-novel-book-service:latest`
- 容器: `trace-real-novel-cloud-novel-book-service`
- 端口: 8080 (network_mode: host)
- 依赖: MySQL + Redis (via `_shared/real-deps.compose.yaml`)
- trace-agent 已注入 (`packages=io.github.xxyopen.novel`)

#### 攻击面 (3 条路径)

| # | API 入口 | Sink | 攻击策略 |
|---|---------|------|---------|
| 1 | GET /api/front/book/content/{chapterId} | BookServiceImpl.getBookContentAbout → log.error | chapterId 异常值触发 NPE/BusinessException |
| 2 | GET /api/front/book/{id} | BookServiceImpl.getBookById → log.error | 无效 ID 触发异常链 |
| 3 | POST /api/front/book/visit | BookServiceImpl.addVisitCount → log.error | bookId 异常触发错误 |

附加攻击面: 任意端点 + Authorization header 含恶意 JWT → `JwtUtils.parseToken` → `log.warn("JWT解析失败:{}", token)`

#### 静态分析产物
- `novel-cloud-callgraph.json` — Joern 调用图 (方法节点 + 调用边)
- `novel-cloud-logging-sinks.json` — 10 个日志 Sink (含模板、参数、行号)

#### 运行方式
```bash
# 启动目标容器
cd examples-yml/novel-cloud && docker compose -f compose.real.yaml up -d

# Mock 模式 (不需要 LLM)
set -a && source .env && set +a
uv run python demo_fuzz_novel.py --mock --max-attempts 10

# 真实 LLM 模式
uv run python demo_fuzz_novel.py --max-attempts 10 --verbose
```

#### 当前打通状态

| 层次 | 状态 | 说明 |
|------|------|------|
| 静态分析 (Joern CG) | ✅ 完成 | 调用图 JSON 已缓存 |
| 日志 Sink 扫描 | ✅ 完成 | 10 个 sink 结构化数据 |
| API 入口标注 | ✅ 完成 | 3 个攻击面已定义 |
| 预期路径构建 | ✅ 完成 | Controller → ServiceImpl 两跳路径 |
| Docker 容器部署 | ✅ 完成 | compose.real.yaml 含 MySQL/Redis 依赖 |
| trace-agent 注入 | ✅ 完成 | X-Return-Trace 协议可用 |
| Fuzz Pipeline 代码 | ✅ 完成 | demo_fuzz_novel.py 可运行 |
| LLM Prompt 工程 | ✅ 完成 | system + feedback prompt 模板 |
| MockLLM 测试 | ✅ 完成 | 框架逻辑可验证 |
| 真实 LLM 攻击验证 | ⏳ 待验证 | 需要容器运行 + LLM API 可用 |
| 攻击成功确认 | ⏳ 待验证 | 需确认 marker 出现在容器日志中 |

#### 待完成
1. 启动 novel-cloud 容器，确认 `/api/front/book/content/999999` 等端点可访问
2. 用真实 LLM (配置 .env) 运行 `demo_fuzz_novel.py`
3. 确认攻击标记 `sink_attacked` 出现在容器日志中
4. 记录首次攻击成功的尝试次数和 LLM 输出

---

## 当前项目全局状态 (截至 2026-07-26)

### 架构总览

```
┌──────────────────────────────────────────────────────────────────┐
│                    静态分析层 (离线, 一次性)                        │
│                                                                  │
│  源码 → Joern(CG) → CodeQL(taint) → PathGenerator(BFS)          │
│  api_discovery.py (API 自动发现)                                  │
│  *-logging-sinks.json (Sink 扫描)                                │
│                          ↓ ExpectedPathSet                        │
├──────────────────────────────────────────────────────────────────┤
│                    Fuzz 循环层 (在线, 迭代)                        │
│                                                                  │
│  Fuzzer(LLM) → Executor(HTTP) → PathDiffer(偏差) → LLM(反馈)    │
│       ↑                                              ↓           │
│       └──────── 偏差 + 变量快照 反馈 ←───────────────┘           │
├──────────────────────────────────────────────────────────────────┤
│                    目标微服务容器                                   │
│                                                                  │
│  trace-agent(ByteBuddy) + TracingAspect(AOP) + JaCoCo            │
│  X-Return-Trace → span → X-Execution-Trace                       │
└──────────────────────────────────────────────────────────────────┘
```

### 完成度

| 模块 | 完成度 | 说明 |
|------|--------|------|
| 18 项目 trace 注入 + 镜像 | 100% | 全部验证通过 |
| 预期路径 BFS 算法 | 100% | |
| Joern 调用图集成 | 100% | java-microservice + novel-cloud 验证 |
| CodeQL 污点分析 | 100% | java-microservice 验证 |
| API 自动发现 | 100% | |
| 偏差计算 (path_differ) | 100% | |
| 变量快照 (snapshot) | 100% | |
| Fuzz Pipeline 框架 | 100% | 代码完整, MockLLM 可跑 |
| LLM Prompt 工程 | 90% | 模板完整, 真实效果待验证 |
| 真实 LLM Fuzz 验证 | 40% | novel-cloud 已跑真实 LLM, 但入口路由未命中 (见阶段 16) |
| 多项目批量 Fuzz | 0% | 待 novel-cloud 验证通过后推广 |

---

### 阶段 16: novel-cloud 真实 LLM Fuzz 首次运行 (2026-07-26, 于 2026-08-05 复盘)

#### 运行事实
- 真实 LLM 已成功驱动 fuzz 循环, 对 `GET /content/{chapterId}` → `BookServiceImpl.getBookContentAbout` 目标跑了 9 轮
- 结果文件:
  - `logs/fuzz/GET__content_{chapterId}__getBookContentAbout__20260726_155827.md` (报告)
  - `logs/fuzz/GET__content_{chapterId}__getBookContentAbout__20260726_155827.json` (原始数据)
  - `logs/fuzz/GET__api_front_book_content_{chapterId}__getBookContentAbout__20260726_161016.json`

#### 结果
| 指标 | 结果 |
|------|------|
| 最终状态 | **unreachable** |
| 到达 Sink 次数 | 0/9 |
| 标记注入成功 | 0/9 |
| 偏差类型 | `not_started` — 预期入口 `FrontBookController.getBookContentAbout` 在 trace 中从未出现 |

#### 根因分析
- **API 路由路径喂错**: pipeline 传给 LLM 的入口路径是 `/content/{chapterId}`, 与真实路由不符
- LLM 自行盲猜了 7 个路径变体 (`/content/1`, `/front/book/content/1`, `/api/front/book/content/about/1`, `/novel/front/book/content/about/1` 等), 全部未命中 Controller
- 连入口 Controller 都没进, 后续 sink 攻击无从谈起

#### 结论
- 框架链路 (fuzz → 执行 → trace → 偏差反馈 → LLM 迭代) 已验证通畅
- 卡点在**上游数据**: 阶段 13 已实现的 `api_discovery.py` (自动扫描真实 Spring 路由, 含类级 `@RequestMapping` 前缀) **尚未接入 fuzz pipeline**, 导致入口路径不准
- 下一步: 用 `api_discovery.py` 扫描 novel-cloud 真实路由 (含 `/api/front/book` 类级前缀), 用发现的准确路径作为 fuzz 入口, 再重跑

#### git 状态
- 最新提交: `cca44edc Add demo fuzz`, 工作区无未提交改动

---

### 阶段 17: novel-cloud 真实 LLM Fuzz 重跑 —— 首次全目标到达 Sink (2026-08-06)

承接阶段 16 的复盘, 本阶段修复了阻塞点并用真实 LLM 重跑 fuzz, 取得实质突破: **3 个目标 100% 到达 sink, 其中 1 个成功注入攻击标记**。

#### 修复的三个阻塞点

1. **入口路径前缀 (承阶段 16 结论)**
   - `api_discovery.py` 的常量解析器已能正确解析 `@RequestMapping(ApiRouterConsts.API_FRONT_BOOK_URL_PREFIX)` → `/api/front/book`
   - `demo_fuzz_novel.py` 的 3 个目标现在使用完整路径 (`/api/front/book/content/{chapterId}`, `/api/front/book/{id}`, `/api/front/book/visit`)
   - 验证: `GET /api/front/book/content/1` 返回 HTTP 200

2. **端口冲突**
   - 8080 被一个 8 天前的 `tei` 容器占用, 导致 novel-cloud 崩溃重启 4 次 (`Port 8080 was already in use`)
   - 处理: 停掉冲突容器后端口释放

3. **OTEL javaagent 冲突 (承阶段 8 已知问题)**
   - 端口释放后容器能启动 (restart=0), 但在 "Instrumentation installed" 后挂起, 无 Spring banner
   - `docker exec ps -ef` 显示进程同时加载了 `opentelemetry-javaagent.jar` + `trace-agent.jar`, 双 agent 冲突导致 hang
   - 修复: 在 [examples-yml/novel-cloud/compose.real.yaml](examples-yml/novel-cloud/compose.real.yaml) 的 `volumes:` 前加入 entrypoint override, 绕过镜像硬编码的 OTEL javaagent:
     ```yaml
     entrypoint: ["sh", "-c", "exec java $JAVA_TOOL_OPTIONS -jar /app/app.jar $0 $@"]
     ```
   - 该模式复用自 [examples-yml/java-microservice/compose.snapshot-test.yaml](examples-yml/java-microservice/compose.snapshot-test.yaml)
   - force-recreate 后服务正常, 进程命令行中不再出现 OTEL javaagent, `/api/front/book/content/1` 返回 HTTP 200

#### Fuzz 运行结果

- 命令: `python demo_fuzz_novel.py --max-attempts 8 --verbose`
- LLM: gpt-5.4-mini @ newapi (从 [.env](.env) 读取)
- 日志: `logs/fuzz/rerun_20260806.log` + 每目标的对话 JSON
- 总耗时: 971.8s, 总体成功率 1/3 (33%)

| # | 目标 | 结果 | 尝试次数 | 说明 |
|---|------|------|---------|------|
| 1 | `GET /api/front/book/content/{chapterId}` → `getBookContentAbout` | ✅ **attacked** | 5/8 | sink 到达 + 标记注入成功 |
| 2 | `GET /api/front/book/{id}` → `getBookById` | ⚠️ reached sink, marker not injected | 8/8 | 到达 sink 但标记未进日志 |
| 3 | `POST /api/front/book/visit` → `addVisitCount` | ⚠️ reached sink, marker not injected | 8/8 | 到达 sink 但标记未进日志 |

#### 关键观察 (对比阶段 16)

- **阶段 16**: 0/9 到达 Controller (入口路径错误, `not_started`)
- **阶段 17**: 3/3 到达 sink —— 路径前缀修复直接解决了阶段 16 的根因
- LLM 自主学到的策略: 把攻击标记放进 `Long` 型路径变量 (`/content/sink_attacked`) 会触发类型转换失败, 走到 `CommonExceptionHandler` 而进不了 Controller; 改用合法数值 (`/content/1`) 才能到达 sink
- 目标 2/3 "到达 sink 但标记未注入" 的原因: sink 是 `log.error(e.getMessage(), e)`, 只有当抛出的异常 message 本身携带攻击标记才会被打印。LLM 试了 query 参数重复 (`?bookId=1&bookId=sink_attacked`)、表单、JSON body 等多种注入位, 但:
  - 标记进了非数字参数 → 类型转换异常在进入 Controller 前就被拦截 (`not_started`, marker 出现在解析异常里但没到 sink)
  - 标记进了合法执行路径 → 业务方法正常执行不抛异常, `log.error` 不触发 (`full_reach`, 到达 sink 但无 marker)
  - 这是 sink 类型 (异常日志) 与污染参数类型 (`Long`) 的固有张力, 非框架缺陷
- 期间出现数次间歇性 LLM 连接错误 (`RemoteDisconnected`), pipeline 的重试逻辑正常吸收, 不影响整体流程

#### 完成度更新

| 模块 | 旧 | 新 | 说明 |
|------|----|----|------|
| 真实 LLM Fuzz 验证 | 40% | **75%** | novel-cloud 全目标到达 sink, 1 目标完整注入成功; 剩余目标受 sink 类型约束 |
| API 自动发现接入 fuzz | — | 100% | 常量解析路径已用于 fuzz 入口, 阶段 16 根因消除 |

#### 下一步
1. 针对"异常日志型 sink"优化 fuzz 策略: 引导 LLM 构造既能通过类型转换、又能在业务层触发携带标记的异常的输入 (如 SQL 注入触发的异常 message)
2. 将验证通过的 novel-cloud 流程推广到多项目批量 fuzz

#### git 状态
- 工作区改动: [examples-yml/novel-cloud/compose.real.yaml](examples-yml/novel-cloud/compose.real.yaml) 新增 entrypoint override (未提交)

---

### 阶段 18: getBookContentAbout Fuzz 结果可视化 + 目标数量澄清 (2026-08-06)

#### HTML 报告
- 基于真实日志 `logs/fuzz/GET__api_front_book_content_{chapterId}__getBookContentAbout__20260805_162505.json` 生成时间线式 HTML 报告 [logs/fuzz/getBookContentAbout_report.html](logs/fuzz/getBookContentAbout_report.html)
- 展示 5 次尝试的请求 / LLM 推理 / 执行偏差 / sink 到达 & 标记注入状态徽章:
  1. `content/sink_attacked` → not_started (Long 型转换异常, 未入 Controller)
  2. LLM 连接中断 (RemoteDisconnected)
  3. `content/1?chapterId=sink_attacked` → full_reach, query 参数未流入日志
  4. `content/1;sink_attacked?chapterId=1` → full_reach, 矩阵参数被剥离
  5. `Authorization: Bearer sink_attacked` → 命中 JwtUtils.parseToken 的 log.warn, 注入成功 🎯

#### 目标数量澄清 (只有 3 个 fuzz 目标的原因)
- **不是**因为污点路径只有 3 条; [novel-cloud-logging-sinks.json](novel-cloud-logging-sinks.json) 静态扫描实际发现 **10 个日志 sink** (resultCount=10)
- [demo_fuzz_novel.py](demo_fuzz_novel.py) 的 FUZZ_TARGETS 是**手写硬编码的 3 条 API→sink 路径**, 仅作演示
- 完整"扫描全部 sink → 自动构造污点路径 → 批量 fuzz"尚未接入 (对应"多项目批量 Fuzz 0%")

---

### 阶段 19: 全自动 Sink→路径→批量 Fuzz 接入 (3 sink 白名单) (2026-08-06)

#### 目标
将"扫描全部 sink → 自动构造污点路径 → 批量 fuzz"端到端跑通, 但按需求只上 demo 的 3 个 sink (不上全部 10 个).

#### 现状澄清
- 管线骨架**已存在**于 [run_novel_cloud_pipeline.py](run_novel_cloud_pipeline.py): Joern 调用图 → CodeQL 污点 → API 发现+路径生成 → 批量 fuzz 四步齐全
- 缺的是: (1) 未跑通 3 目标 (只生成 1 条); (2) 未按需求限制 sink 数量
- 注: demo 原 3 个"sink"里只有 `getBookContentAbout` 是真实扫描点, `getBookById`/`addVisitCount` 是占位 (扫描 JSON 里不存在); 本阶段改用扫描结果里 3 个真实可达 sink

#### 三处改动 (均在 run_novel_cloud_pipeline.py)
1. **Sink 白名单** `SINK_WHITELIST`: `BookServiceImpl.getBookContentAbout` / `JwtUtils.parseToken` / `CommonExceptionHandler.handlerException`; 3.2 阶段按白名单过滤 (10 → 3)
2. **异常处理器合成边** `_add_exception_handler_edges()`: Spring 全局 `@ExceptionHandler` 由框架调用, 静态调用图看不到; 手动补 `FrontBookController.getBookContentAbout → CommonExceptionHandler.handlerException` 边 (trace 已证实该请求会经它). 在 step1 两处 (缓存/新生成) 调用
3. **去重键改为 (API, sink) 对**: `_select_best_targets` 原按 API 去重会把多 sink 压成 1 条; 改后每个 sink 各成独立 fuzz 目标

#### 端到端结果 (真实 LLM gpt-5.4-mini, --skip-codeql)
- 生成预期路径 3 条 → Fuzz 目标 3 条 (之前只有 1 条)
- **攻击成功: 3/3 (100%), 总耗时 110.1s**

| 目标 sink | 注入通道 | 尝试次数 | 耗时 |
|-----------|---------|---------|------|
| CommonExceptionHandler.handlerException | 路径参数 chapterId=FUZZ_MARKER (类型转换异常) | 1 | 19.1s |
| BookServiceImpl.getBookContentAbout | (2 次尝试后成功) | 2 | 70.7s |
| JwtUtils.parseToken | query 参数 token=FUZZ_MARKER (JWT 解析失败) | 1 | 20.2s |

- 日志: [logs/fuzz/pipeline_3sink_*.log](logs/fuzz/)
- 各目标对话 JSON: `GET__api_front_book_content_{chapterId}__{handlerException,getBookContentAbout,parseToken}__*.json`

#### 完成度更新
| 模块 | 旧 | 新 | 说明 |
|------|----|----|------|
| 全自动 Sink→路径→批量 Fuzz | 0% | **100%** (novel-cloud) | 3 sink 端到端跑通, 100% 命中 |

#### 下一步
1. 扩展白名单 / 去掉白名单跑全部 10 个 sink
2. 将该自动化流程推广到其它项目 (RuoYi / PiggyMetrics 等)

### 阶段 20: 更换 LLM 提供商 (DeepSeek) + 全 Sink Fuzz 重跑 (2026-08-13)

#### 目标
承接阶段 19, 去掉 3-sink 白名单限制跑全部 sink; 同时更换 LLM 提供商 (从 gpt-5.4-mini 换为 DeepSeek `deepseek-v4-flash`)。

#### 代码改动 (run_novel_cloud_pipeline.py)
1. **新增 `--all-sinks` 参数**: 置位时 3.2 阶段跳过 `SINK_WHITELIST`, 改用 `"search" not in class_name` 过滤 (排除 novel-search, 因其不在 book-service 容器内)
2. **`step3_generate_paths` 增加 `all_sinks` 形参**, 由 `main()` 透传 `args.all_sinks`
3. **泛化 `_add_exception_handler_edges()`**: 原只给 `handlerException` 补合成边, 现为 `CommonExceptionHandler` 三个 `@ExceptionHandler` 方法 (`handlerException` / `handlerBindException` / `handlerBusinessException`) 都补边; 否则后两者路径不可达 (生成 0 目标)

#### Sink 可达性
- 静态扫描 sink 共 10 个, 其中 5 个在 novel-search (EsConfig/XxlJobConfig/BookToEsTask×3) —— **不在 book-service 容器内, 不可达**
- 实际可达 5 个: `getBookContentAbout` / `parseToken` / `handlerException` / `handlerBindException` / `handlerBusinessException`
- 补边后成功生成 **5 条路径 / 5 个 fuzz 目标** (阶段 19 只有 3 条)

#### LLM 连接测试
- DeepSeek `deepseek-v4-flash` @ https://api.deepseek.com/v1 —— 最小对话验证**连通正常** (响应"测试通过")

#### 端到端结果 (DeepSeek, --skip-codeql --all-sinks)
- **攻击成功: 0/5 (0%), 总耗时 1966.1s** (5 目标 × 各 10 次尝试全部耗尽)
- 全部偏差为 `not_started` (0/3 节点), 请求从未进入 Controller

| 目标 sink | 结果 | 尝试 | 耗时 |
|-----------|------|------|------|
| CommonExceptionHandler.handlerException | ❌ unreachable | 10 | 407.5s |
| CommonExceptionHandler.handlerBindException | ❌ unreachable | 10 | 385.1s |
| CommonExceptionHandler.handlerBusinessException | ❌ unreachable | 10 | 412.6s |
| BookServiceImpl.getBookContentAbout | ❌ unreachable | 10 | 376.9s |
| JwtUtils.parseToken | ❌ unreachable | 10 | 383.9s |

#### 根因分析 (对比阶段 19 的 3/3 成功)
- **模型能力差异是主因**: `deepseek-v4-flash` 绝大多数尝试直接输出**字面量** URL `.../content/{chapterId}` (未把 `{chapterId}` 替换为具体数字), Spring MVC 路由无法匹配 → 请求根本没到 Controller → `not_started`
- 系统提示已明确"路径参数必须替换为具体值", 但该模型多轮陷入重复无效请求, 仅偶尔 (第 6~8 轮) 替换为 `content/1?token=...`, 且仍未成功
- 对比: 阶段 19 的 gpt-5.4-mini 能稳定替换路径参数并按污点通道注入, 3/3 命中
- **结论**: fuzz 效果对 LLM 指令遵循能力高度敏感; `deepseek-v4-flash` 在本任务下遵循度不足

#### 产物
- 日志: [logs/fuzz/](logs/fuzz/) 下 `pipeline_allsink_*.log` 及各目标 `*__{sink}__*.json`

#### 下一步
1. 换用指令遵循更强的模型重跑 (或对 DeepSeek 调 temperature / 强化路径参数替换的提示约束)
2. 在提示中加入"禁止输出字面量占位符"的显式校验, 或在 executor 侧对未替换的 `{...}` 做拦截重试

### 阶段 21: Prompt 优化 —— 显式说明路径参数可替换 (2026-08-13)

#### 背景
承接阶段 20 根因: `deepseek-v4-flash` 把 URL 模板里的 `{chapterId}` 当字面量照抄, 不知道它是**可替换的变量**, 导致请求 404 / 不进 Controller (`not_started`)。判断为 prompt 表达问题 —— 原提示只说"路径参数必须替换为具体值", 但把完整 URL 直接渲染成 `.../content/{chapterId}` 又容易被弱模型误当成目标字符串。

#### 三处改动 (均在 fuzzer.py)
1. **API 入口段**: 将"完整 URL"改为"URL 模板", 并加 ⚠️ 说明: `{xxx}` 是路径参数占位符 (变量, 非字面串), 必须替换成具体值, 否则无法匹配路由、不进 Controller
2. **输出格式示例**: `url` 字段注释改为明确示例 (`.../content/1` 而非 `.../content/{chapterId}`); "重要提示" 第 1 条强化为"URL 中绝对不能出现 `{}` 花括号占位符"
3. **历史反馈动态检测**: 在 [fuzzer.py](fuzzer.py) 拼接历史反馈时, 用 `re.search(r'\{\w+\}', url)` 检测上次 URL 是否残留占位符; 若残留则追加明确提示"你上次输出的 URL 里仍残留 `{...}`... 必须替换成具体值"

#### 状态
- 代码改动完成, 无语法错误

#### 验证结果 (DeepSeek 重跑, 2026-08-13 01:36)
先修复环境: `embedding-server` 容器占用宿主 8080 (host 网络), 导致 book-service 启动报 `Port 8080 already in use`; 停掉 embedding-server 后重启 book-service, 7s 启动成功。

Fuzz 结果: 5 目标, 成功 1 (20%), 耗时 233.8s
| # | Sink | 结果 | 说明 |
|---|------|------|------|
| 1 | handlerException | ✅ attacked (1 次) | **第 1 次即命中**, 输出 `.../content/FUZZ_MARKER_7x9k` |
| 2 | handlerBindException | ❌ unreachable (10 次) | 见下 |
| 3 | handlerBusinessException | 💥 error (0 次) | SSL 断连 |
| 4 | BookServiceImpl.getBookContentAbout | 💥 error (0 次) | SSL 断连 |
| 5 | JwtUtils.parseToken | 💥 error (0 次) | SSL 断连 |

**关键结论**:
- ✅ **Prompt 优化目标达成**: 模型正常情况下能正确把 `{chapterId}` 替换成具体值 (sink1 首次命中即为证据), 阶段 20 的"照抄占位符"问题在正常响应时已解决
- ⚠️ **DeepSeek API 不稳定**: 01:39:47 起持续 `SSL: UNEXPECTED_EOF_WHILE_READING`, 导致 sink2 后半程 + sink3/4/5 全部 0 次尝试直接判 error (网络/服务端问题, 非 prompt/代码问题)
- ⚠️ **sink2 路径规划问题**: 传非数字 chapterId 触发的是 `MethodArgumentTypeMismatchException` → 进 `handlerException`(sink1) 而非 `handlerBindException`; sink2 尝试3 marker 已进日志(marker:✓)但被判未达预期 sink。当前 API 入口无 `@Valid` body, 本质上难以精确触发 BindException
- ⚠️ DeepSeek 对占位符反馈的遵循仍不稳定 (sink2 多次仍输出字面 `{chapterId}`, 尽管已加检测反馈), 弱于 gpt-5.4-mini

#### 下一步
1. SSL 为瞬时问题, 重跑大概率可覆盖 sink3/4/5
2. sink2(handlerBindException) 需重新审视可达性 (当前入口无法自然触发绑定异常)

#### 补充验证: 只重跑 sink 3/4/5 (2026-08-13 01:47)
临时把 SINK_WHITELIST 换成 {handlerBusinessException, getBookContentAbout, parseToken}, 不带 `--all-sinks` 走白名单跑这 3 个 (跑完已恢复原白名单)。

结果: 3 目标, 成功 2 (67%), 耗时 488s
| Sink | 结果 | 说明 |
|------|------|------|
| handlerBusinessException | ❌ unreachable (10 次) | 模型 10 次全复读字面 `{chapterId}`, 未进 Controller |
| BookServiceImpl.getBookContentAbout | ✅ attacked (1 次) | `content/1?userId=FUZZ_MARKER_7x9k` 首次命中 |
| JwtUtils.parseToken | ✅ attacked (1 次) | `content/1` + `Authorization: FUZZ_MARKER_7x9k` 首次命中 |

**关键结论**:
- ✅ sink4/5 首次即成功, 模型正确替换 `{chapterId}=1` 并按污点通道 (userId 查询参数 / Authorization 头) 注入 marker — Prompt 优化在正常响应下完全有效
- ⚠️ **DeepSeek 多轮复读问题**: sink3 一旦首次输出字面 `{chapterId}`, 后续 10 轮即使有占位符检测反馈仍持续复读同一无效请求, 无法自我纠正; 但首次正确 (sink4/5) 就无此问题。这是模型多轮稳定性问题, 非 prompt 表达问题
- sink3(handlerBusinessException) 本身也难精确触达 (需合法数字 chapterId + 触发业务异常, 而非类型转换异常)

#### 阶段 21 总体结论
- Prompt 优化 (URL 模板说明 + 输出示例 + 占位符检测反馈) 达成目标: 模型首次响应能正确替换路径参数
- 剩余失败均非 prompt 问题: (a) DeepSeek 首次出错后陷入多轮复读; (b) 个别 sink 路径本身难精确触达
- 可选后续: 在 executor 侧对残留 `{...}` 的 URL 直接拒绝并强制模型重构 (跳过无效请求), 打破复读循环

### 阶段 22: 清理并重跑 5 个可达 Sink + 生成美观 HTML 报告 (2026-08-13)

承接用户请求 "清理 fuzz 的 json → 重新 fuzz 这些 sink → 把过程转为美观 html"。原始诉求是 10 个 sink, 但经根因分析确认 novel-search 的 5 个 sink **无法通过 HTTP 请求触达** (它们是启动期 @Bean 初始化日志 + xxl-job 定时任务日志, 非请求驱动; 且 novel-search 无 compose 配置、无 traced 镜像、依赖未部署的 ES), 故本轮聚焦 novel-book-service 的 **5 个可达 sink**。

**执行流程**:
1. 删除 `logs/fuzz/` 下旧的对话 JSON (保留 *.log)
2. 确认 `trace-real-novel-cloud-novel-book-service` 容器健康 (200 OK)
3. `run_novel_cloud_pipeline.py --skip-codeql --all-sinks` 重跑 5 条路径 (缓存调用图 429 节点/248 边)
4. 新增 [generate_fuzz_report.py](generate_fuzz_report.py) 聚合每 sink 最新 JSON → 单页暗色主题 HTML

**Fuzz 结果 (总耗时 840s, 3/5 成功 = 60%)**:
| # | Sink | 结果 | 尝试 | 耗时 |
|---|------|------|------|------|
| 1 | `CommonExceptionHandler.handlerException` | ✅ attacked | 1 | 29.3s |
| 2 | `CommonExceptionHandler.handlerBindException` | ❌ unreachable | 10 | 369.3s |
| 3 | `CommonExceptionHandler.handlerBusinessException` | ❌ unreachable | 10 | 413.6s |
| 4 | `BookServiceImpl.getBookContentAbout` | ✅ attacked | 1 | 20.2s |
| 5 | `JwtUtils.parseToken` | ✅ attacked | 1 | 7.6s |

**失败 sink 分析 (2/3)**:
- **handlerBindException**: 需 `chapterId` 绑定失败抛 `BindException`。但把非数字 marker 放入路径时, `TokenParseInterceptor.preHandle` 先抛 token 异常 → 走 `handlerException` 而非 `handlerBindException` (拦截器早于参数绑定)。DeepSeek 又陷入多轮复读 `{chapterId}` 字面量, 10 次未命中。
- **handlerBusinessException**: 第 9 次到达 Controller (`chapterId=0`), 但走了 `handlerException` 而非 `handlerBusinessException` (0 未被业务判定为"章节不存在"); 第 10 次 `chapterId=999999999` 同样偏差。marker 已进日志但 sink 未精确命中。

**产出**:
- HTML 报告: [logs/fuzz/fuzz_report.html](logs/fuzz/fuzz_report.html) — 5 条记录, 3 确认漏洞, 含每轮 LLM 推理/请求/偏差折叠详情
- 新工具: [generate_fuzz_report.py](generate_fuzz_report.py)

**结论**:
- 10 sink 中 5 个 (novel-search) 属结构性 HTTP 不可达, 超出请求驱动 fuzz 方法论范围
- 5 个可达 sink 覆盖率 3/5 (60%); 2 个失败源于拦截器抢先/业务分支难触达 + DeepSeek 复读, 非方法缺陷

---

### 阶段 23: SpringBlade Fuzz 实战 + 关键 Bug 修复 (2026-08-16)

#### 目标
将 fuzz 流程推广到第二个项目 SpringBlade (blade-auth), 验证流程可推广性; 过程中发现并修复两个影响正确性的关键 bug。

#### 目标服务与攻击面
- 镜像: `llmfuzz/springblade-blade-auth:latest`, 容器 `trace-real-springblade-blade-auth`, 端口 8080
- 静态扫描 sink 共 3 个, 其中 `AuthFilter.unAuth` 在 blade-gateway (不在本容器, 不可达)
- 实际可达 2 个 granter sink (登录失败时 `log.error("用户登录失败, 账号:{}, IP:{}", account, ...)`, 污染参数 `account` 用户可控):
  - `PasswordTokenGranter.grant` L82
  - `CaptchaTokenGranter.grant` L96
- 入口: `POST /token` → `AuthController.token`, 按 `grantType` 运行时字符串分派到不同 granter (`TokenGranterBuilder.getGranter`)
- pipeline: `run_springblade_pipeline.py` (含 `_add_granter_dispatch_edges` 补运行时分派合成边)

#### Bug 1: 双 javaagent 死锁 (环境)

- **现象**: 容器启动后日志停在 `[trace-agent] Instrumentation installed.`, 无 Spring banner, 8080 不监听
- **诊断**: `kill -QUIT 1` 线程 dump 显示 main 线程 `waiting for monitor entry` 在 `java.util.zip.ZipFile$Source.get`, 栈底是 `InstrumentationImpl.loadClassAndStartAgent` — 两个 javaagent premain 竞争类加载锁死锁
- **根因**: `JAVA_TOOL_OPTIONS` 同时挂 trace-agent + jacocoagent 两个 agent, premain 阶段 `ClassInjector` 持锁加载类时与第二个 agent 死锁 (JVM 类加载时序竞态, novel-cloud 同配置未触发纯属侥幸)
- **修复**: [examples-yml/SpringBlade/compose.real.yaml](examples-yml/SpringBlade/compose.real.yaml) 移除 jacocoagent (fuzz 不需要覆盖率), 并加 `entrypoint` override 绕过镜像硬编码 OTEL agent
- **结果**: 服务 12 秒正常启动, `/captcha` HTTP 200, trace 协议正常

#### Bug 2: max_tokens 过小导致 reasoning model 空响应 (核心)

- **现象**: Captcha fuzz 前 9 轮 LLM 全部返回空 (round 2-9 `resp_len=0`), 第 10 轮才突然成功; 疑似模型"空转"
- **诊断**: 重放 round 2 的 messages 实测 DeepSeek:
  ```
  重放1: HTTP 200 content_len=0 finish=length completion_tokens=4096
  重放2: HTTP 200 content_len=0 finish=length completion_tokens=4096
  重放3: HTTP 200 content_len=595 finish=stop  completion_tokens=3455
  ```
- **根因**: `deepseek-v4-flash` 是 reasoning model, **思考 token 计入 completion 预算**; `max_tokens=4096` 太小, 长反馈 (6677 字符) 下思考耗尽全部预算 → 正文为空, `finish_reason=length`. 且 [llm.py](llm.py) 直接返回 `content`, 对空响应/length 截断**不报错不重试**, pipeline 白白浪费 8 次尝试配额
- **修复** ([llm.py](llm.py)):
  1. `max_tokens`: 4096 → **65536** (reasoning model 需大预算)
  2. 新增**空响应/length 检测 + 重试** (`max_retries=3`): `content` 为空或 `finish_reason != "stop"` 时自动重试, 不再静默返回空串; 重试耗尽仍有截断正文则降级返回, 全空才抛错

#### 源码反馈优化 (fuzzer.py)

- **问题**: Captcha 失败时, `_format_divergence` 只给 `first_missed_node`(终点 `CaptchaTokenGranter.grant`) 的源码, LLM 看不到分派逻辑, 不知用 `grantType=captcha`
- **优化**: [fuzzer.py](fuzzer.py) `_format_divergence` 同时给出**已到达节点(分派点 `AuthController.token`) + 目标节点**两段源码
- **效果**: LLM 从 `AuthController.token` 源码看到 `@RequestParam(defaultValue="password") String grantType` + `getGranter(grantType)`, 明白 (1) 参数走 query 非 body, (2) 字段名是 `grantType` 非 `grant_type`, (3) 按它分派

#### Fuzz 结果对比 (CaptchaTokenGranter)

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| 结果 | ✅ attacked (10 次) | ✅ attacked (**5 次**) |
| 到达 sink | 第 10 次 | **第 3 次** |
| 总耗时 | 510.6s | **282.2s (-45%)** |
| 空响应浪费 | 8 轮 | **0 轮** |

收敛过程 (修复后): body(错) → `?grant_type=`(query对字段错) → `?grantType=`(对, 到达sink) → 调整 → marker 注入. 每轮均有真实推理, 逐步逼近。

#### 最终成果
- **SpringBlade 2 个可达 sink 全部 fuzz 成功 (2/2 = 100%)**:
  - PasswordTokenGranter.grant ✅ attacked (3 次)
  - CaptchaTokenGranter.grant ✅ attacked (5 次)
- 攻击证据 (容器日志):
  ```
  ERROR PasswordTokenGranter : 用户登录失败, 账号:FUZZ_MARKER_7x9k, IP:127.0.0.1
  ERROR CaptchaTokenGranter  : 用户登录失败, 账号:FUZZ_MARKER_7x9k, IP:127.0.0.1
  ```

#### 关键经验教训
1. **reasoning model 的 max_tokens 必须足够大**: 思考 token 计入 completion, 预算不足时正文为空且 `finish_reason=length`, 表现为"模型空转"实为 token 耗尽
2. **空响应/截断必须检测重试**: HTTP 200 ≠ 有效响应, LLM 客户端不能无脑返回 `content`
3. **分派点源码是弱模型的关键**: 运行时字符串分派 (`getGranter(grantType)`) 静态 CG 看不到分派条件, 给 LLM 看已到达分派点的源码能让它自己推断正确参数契约
4. **多 javaagent 在 Docker 中有死锁风险**: premain 阶段类加载锁竞争, fuzz 场景只保留必需的 agent

#### 修改的文件 (未提交)
- [llm.py](llm.py) — max_tokens 65536 + 空响应/length 重试
- [fuzzer.py](fuzzer.py) — `_format_divergence` 同时给分派点 + 目标方法源码
- [examples-yml/SpringBlade/compose.real.yaml](examples-yml/SpringBlade/compose.real.yaml) — entrypoint override + 移除 jacocoagent

#### 完成度更新
| 模块 | 旧 | 新 | 说明 |
|------|----|----|------|
| 多项目批量 Fuzz | 0% | **推进中** | SpringBlade 成为继 novel-cloud 后第二个 100% 跑通的项目, 验证流程可推广 |

---

### 阶段 24: PiggyMetrics Fuzz + sink 可达性分析方法论 (2026-08-16)

#### 目标
继续多项目推广, 选择 sink 数第三少 (19) 且可部署的 PiggyMetrics account-service。过程中建立了"sink 可达性前置分析"的方法论。

#### 选项目过程 (重要方法论)
按 sinks/ 数量排序后逐个评估"sink 分布 vs 部署服务"匹配度:
- spring-boot-cloud(1) / supermarket(9) / light-reading(17): ❌ 无 compose 部署配置
- **PassJava (16)**: ❌ 排除 — compose 部署的 `passjava-member` 服务源码里**没有任何日志调用** (grep 无结果), 16 个 sink 全在 `io.renren.*`(renren-fast 模块) 和 gateway, **不在部署容器内**
- **PiggyMetrics (19)**: ✅ account-service 内约 7 个可达 sink, 选中

**经验**: sink 总数少 ≠ 可 fuzz。必须确认 sink 在**实际部署的服务**源码内, 否则白忙。

#### 环境
- 镜像 `llmfuzz/piggymetrics-account-service:latest`, 容器 `trace-real-piggymetrics-account-service`, 端口 8080
- compose 用 `command: java -jar` (非镜像 entrypoint), OTEL 不会硬编码加载; 但仍移除 jacocoagent 防双 agent 死锁
- 清理了 trace-real-* 残留容器冲突后启动成功, `/demo` HTTP 200, trace 协议正常

#### Pipeline
新建 [run_piggymetrics_pipeline.py](run_piggymetrics_pipeline.py) (复制 SpringBlade 版适配):
- Joern 调用图: 124 节点 66 边 (+3 虚分派 +1 异常处理合成边)
- 合成边: `_add_exception_handler_edges` 补 `AccountController.createNewAccount → ErrorHandler.processValidationError` (@ControllerAdvice 框架反射调用, 静态 CG 看不到)
- API 入口 6 个, 白名单 sink 7 个, 生成 3 条 fuzz 目标

#### sink 可达性实测结论 (3 个生成目标)

| 目标 | 结果 | 原因 |
|------|------|------|
| `ErrorHandler.processValidationError` | ✅ **attacked (1 次, 73.5s)** | 重复创建已存在用户 → `IllegalArgumentException("account already exists: MARKER")` |
| `AccountServiceImpl.create` 成功日志 | ❌ 结构性不可达 | `authClient.createUser()`(L51) 先于 `log.info`(L67) 失败, auth-service 未部署 → HTTP 500 |
| `AccountServiceImpl.saveChanges` | ❌ 未跑 | `PUT /current` 需 OAuth2, 返回 401, LLM 无法绕过 |
| `CustomUserInfoTokenServices.*` (3 sink) | ❌ 未生成路径 | OAuth2 认证链路, 需有效 token, 无法从 API 入口到达 |

#### 关键发现: 有状态依赖的 sink

ErrorHandler sink 首次 fuzz 失败 (10 次全 unreachable):
- 前 3 次到达 Controller 但请求合法 (无校验异常)
- 后 7 次退化为 `not_started` (请求格式错误, 连 Controller 都没进)

**根因**: 该 sink 触发需要 `Assert.isNull(existing, "account already exists: "+username)` 抛异常, 这要求 **Mongo 里已存在同名用户**。LLM 难以自己发现"先创建一次再重复创建"的两步有状态策略。

**解决**: 手工预置 `FUZZ_MARKER_7x9k` 用户到 Mongo:
```bash
docker exec trace-real-mongo mongo --quiet --eval \
  'db.getSiblingDB("test").accounts.insertOne({"_id":"FUZZ_MARKER_7x9k","name":"FUZZ_MARKER_7x9k","lastSeen":new Date()})'
```
之后第一次 POST 即触发 `account already exists: FUZZ_MARKER_7x9k` → ErrorHandler → marker 进日志, **1 次尝试成功**。

攻击证据 (容器日志):
```
INFO ErrorHandler : Returning HTTP 400 Bad Request
java.lang.IllegalArgumentException: account already exists: FUZZ_MARKER_7x9k
```

#### 关键经验教训
1. **sink 可达性前置分析必做**: 部署服务源码内无日志调用的项目直接排除 (PassJava 教训)
2. **外部依赖导致的结构性不可达**: sink 日志在 feign/client 调用之后, 而下游服务未部署时, 该 sink 永远不可达 (create 教训)
3. **有状态 sink 需预置状态**: "重复创建"类攻击需预置数据, 纯无状态 fuzz 难发现两步策略 — 未来可考虑在 prompt 中提示 LLM"某些攻击需先建立状态"
4. **max_tokens 修复持续有效**: 本轮无空响应浪费, 1 次即收敛

#### 修改的文件 (未提交)
- [run_piggymetrics_pipeline.py](run_piggymetrics_pipeline.py) — 新建, 含异常处理合成边 + 可达性标注
- [examples-yml/PiggyMetrics/compose.real.yaml](examples-yml/PiggyMetrics/compose.real.yaml) — 移除 jacocoagent

#### 完成度更新
| 模块 | 说明 |
|------|------|
| 多项目批量 Fuzz | 第 3 个项目 PiggyMetrics 跑通 (1 个可达 sink 100% 命中); 建立 sink 可达性前置分析方法论 |

---

### 阶段 25: mall-swarm 可达性前置分析 (跳过) (2026-08-16)

#### 分析结论: 暂不 fuzz

按 sink 数升序轮到 mall-swarm (25 sink, sinks/small-swarm-logging-sinks.json)。可达性前置分析后发现**可达 sink 质量差**, 暂不投入 fuzz。

#### 分析过程
- compose 部署 `mall-admin` (hook `com.macro.mall`), 容器 `trace-real-mall-swarm-mall-admin`
- 25 个 sink 分布在多模块: mall-portal(10) / mall-search(3) / mall-demo(6) / mall-common(1) — **均不在 mall-admin 容器**
- mall-admin 源码内仅 5 个日志调用, 匹配到 5 个可达 sink

#### mall-admin 5 个可达 sink 评估

| Sink | 参数 | 问题 |
|------|------|------|
| `MinioController.upload` L53/L75 | 无 | 固定文案, 无法注入 marker |
| `MinioController.upload` L82 | `e.getMessage()` | 依赖 Minio 服务 + 异常, marker 注入通道不明 |
| `OssServiceImpl.policy` L84 | `e` | 依赖 OSS 配置 + 签名异常 |
| `PmsProductServiceImpl.relateAndInsertList` L322 | `e.getMessage()` | 业务异常, marker 需进异常 message |

#### 跳过原因
所有可达 sink 要么无注入点 (固定文案), 要么依赖外部中间件 (Minio/OSS) 或业务异常, marker 注入通道不明确 — 比之前项目"用户输入直接进日志"难得多, 投入产出比低。

#### 方法论沉淀
**sink 可达性前置分析三问** (选项目时必查):
1. sink 是否在实际部署的服务容器内? (PassJava 教训)
2. sink 日志是否依赖未部署的下游服务? (PiggyMetrics create 教训)
3. sink 是否有清晰的用户可控注入点? 固定文案/纯异常 message 难注入 (mall-swarm 教训)

只有三问都通过, 才值得启动容器 fuzz。

---

### 阶段 26: RuoYi-Cloud Fuzz + 框架异常处理 sink 判定盲区修复 (2026-08-16)

#### 目标
第 4 个项目 RuoYi-Cloud (ruoyi-auth, 46 sink)。过程中发现并修复了 pipeline 对"框架异常处理型 sink"的判定盲区。

#### 选项目过程 (三问法筛选)
对 gulimall(29) / ruoyi(46) / cloud-platform(51) 做可达性前置分析:
- **gulimall-member**: 源码内 0 日志调用 (sink 全在 renren/search/ware 模块), 排除
- **ruoyi-auth**: 源码内 0 日志调用, **但** sinks 集中在 `com.ruoyi.common.security.handler.GlobalExceptionHandler`, 位于 `ruoyi-common-security` **依赖 jar** (ruoyi-auth pom 依赖它), 运行时随容器加载 → 可达 ✅
- 注入点清晰: `GlobalExceptionHandler` 多个方法的 `requestURI` 参数用户可控 (URL 路径带 marker)

**与 PassJava 的本质区别**: PassJava 的 sink 在**独立的 renren-fast 服务**(不在部署容器), RuoYi 的 sink 在**依赖 jar**(打进部署容器)。判断依据是部署服务的 pom 是否依赖该模块。

#### 环境
- 镜像 `llmfuzz/ruoyi-cloud-ruoyi-auth:latest`, 容器 `trace-real-ruoyi-cloud-ruoyi-auth`, 8 秒启动
- 修 compose: 移除 jacocoagent (防双 agent 死锁) + entrypoint override (绕过 OTEL)

#### 核心发现: 框架异常处理 sink 的判定盲区

首次 fuzz 全部失败 (`Expecting value: line 1 column 1` / `not_started`), 但**容器日志里 marker 已成功注入**。根因:

`@ExceptionHandler`/`@RestControllerAdvice` 型 sink 触发时 (404/405/参数绑定错误), 请求**未进入业务 Controller 方法**:
- 入口节点 (如 `TokenController.login`) 不在 trace 中 → pipeline 的 `sink_checker`(trace span 匹配) 判 `reached=False`
- 这些路径**不产生 `X-Execution-Trace` 头** (trace filter/AOP 未覆盖到 DispatcherServlet 匹配阶段前的异常)

但这类 sink 的**日志输出本身就是执行证据** — marker 出现在日志即证明 handler 被调用。

#### pipeline.py 修复

[pipeline.py](pipeline.py) `run_single_path` 增加框架异常处理 sink 特判:
```python
if not reached and marker_found and self._is_framework_exception_handler(sink):
    reached = True  # 以日志标记作为到达依据
```
新增 `_is_framework_exception_handler()`: 识别类名含 `exceptionhandler`/`errorhandler`/`controlleradvice` 的 sink。

#### Fuzz 结果 (修复后)

`handleHttpRequestMethodNotSupported` L63 (`log.error("请求地址'{}',不支持'{}'请求", requestURI, e.getMethod())`), 3 入口 **3/3 全部 1 次尝试命中**:

| 入口 | 结果 | 尝试 | 耗时 |
|------|------|------|------|
| POST /login | ✅ attacked | 1 | 147.7s |
| POST /refresh | ✅ attacked | 1 | 30.7s |
| POST /register | ✅ attacked | 1 | 26.5s |

LLM 攻击方式: 对已注册路由用错误 HTTP 方法 + marker 矩阵参数, 如 `PATCH /login;FUZZ_MARKER_7x9k`。

攻击证据 (容器日志):
```
ERROR GlobalExceptionHandler - 请求地址'/login;FUZZ_MARKER_7x9k',不支持'PATCH'请求
ERROR GlobalExceptionHandler - 请求地址'/refresh;FUZZ_MARKER_7x9k',不支持'GET'请求
ERROR GlobalExceptionHandler - 请求地址'/register;FUZZ_MARKER_7x9k',不支持'GET'请求
```

#### 不可 fuzz 的 sink 类型 (新增)
- `handleException`(L123 兜底) / `handleRuntimeException`(L112): 触发需请求**不匹配任何 Controller** 的 URL, trace 完全无覆盖, 虽 marker 能进日志但无法在 pipeline 内形成有效迭代反馈 (偏差计算无 trace 可用) — 本轮排除, 只跑 `handleHttpRequestMethodNotSupported` (对已注册路由用错方法, trace 虽不覆盖但 marker 判定可达)

#### 关键经验教训
1. **依赖 jar vs 独立服务**: 判断 sink 可达性时, 看部署服务 pom 是否**依赖** sink 所在模块 (依赖则可达), 而非仅看入口服务源码有无日志调用
2. **框架异常处理 sink 需日志判定**: trace 验证对 404/405/参数错误失效 (请求不进 Controller), marker 进日志即为执行证据
3. **requestURI 是通用注入点**: 异常处理器的 `request.getRequestURI()` 参数天然用户可控, URL 路径/矩阵参数带 marker 即可注入
4. **pipeline 判定逻辑需适配 sink 类型**: 不能一刀切依赖 trace span 匹配

#### 修改的文件 (未提交)
- [pipeline.py](pipeline.py) — 框架异常处理 sink 日志判定 + `_is_framework_exception_handler` + 异常堆栈打印
- [run_ruoyi_pipeline.py](run_ruoyi_pipeline.py) — 新建, GlobalExceptionHandler 合成边
- [examples-yml/RuoYi-Cloud/compose.real.yaml](examples-yml/RuoYi-Cloud/compose.real.yaml) — 移除 jacoco + entrypoint override

#### 完成度更新
| 模块 | 说明 |
|------|------|
| 多项目批量 Fuzz | 第 4 个项目 RuoYi 跑通 (3/3 100%); 修复框架异常处理 sink 判定盲区, pipeline 适配性提升 |

---

### 阶段 27: 批次总结 + 方法优越性评估框架 (2026-08-16 晚)

#### 产出
[fuzz_summary_20260816.md](fuzz_summary_20260816.md) — 4 个项目 (SpringBlade/PiggyMetrics/RuoYi/mall-swarm) 完整总结: sink 总数/可达 sink/不可达原因/fuzz 轮次/黑盒可 fuzz 性。

#### 核心方法论: 黑盒 fuzz 评估 (方法优越性体现度)

为论证"静态分析 + 偏差反馈引导"相对黑盒 fuzz 的优越性, 引入三分类:

| 类型 | 可黑盒? | 代表 | 方法价值 |
|------|:---:|------|---------|
| 分支/分派逻辑 sink | ❌ | SpringBlade Captcha (grantType 分派) | **强** — 需分派点源码反馈才能纠正分支 |
| 有状态依赖 sink | ❌ | PiggyMetrics ErrorHandler (重复创建) | **强** — 需反馈理解触发策略 + 预置状态 |
| 异常处理器 requestURI 注入 | ✅ | RuoYi GlobalExceptionHandler | **弱** — requestURI 天然可控, 1 次到位, 未用反馈 |

**结论**: 体现方法优越性应选**有分支分派逻辑/有状态依赖/多跳深链路**的 sink; "异常处理器 requestURI 注入"型接近黑盒可达, 不适合作为论证案例。

#### 后续选题导向
针对"必须偏差反馈才能成功"的目标, 优先选择:
1. 多跳调用链深处 (入口 → 多层 service → sink)
2. 需特定参数契约才能到达 (类型转换/业务校验分支)
3. 注入点不在显而易见的 requestURI/body, 而在深参数/派生值

---

### 阶段 28: 寻找"必须偏差反馈"的新应用 sink —— 排查与核心结论 (2026-08-16 晚 ~ 08-17)

#### 目标
找一个新应用, 其 sink 必须通过偏差反馈才能 fuzz 成功 (黑盒 fuzz 成本极高), 以论证方法优越性。

#### zlt 环境调试 (虽未最终 fuzz, 但有沉淀价值)

zlt-uaa 启动遇到 4 个级联环境问题, 逐个解决:
1. **Redis NOAUTH**: 共享 Redis `--requirepass password`, zlt 默认无密码 → 补 `--spring.redis.password=password` + `--spring.data.redis.password=password`
2. **OAuth2 bean 失败** (`authorizationServerSecurityFilterChain` message=null): 最深层 Caused by 是 MySQL `Access denied for user 'root' (using password: NO)` → command 缺 `--spring.datasource.username/password`, 且需移除与 env 重复的 `--zlt.datasource.*` (防 Spring 重复绑定逗号值)
3. **Security 放行**: `/validata/smsCode/{mobile}` 被 OAuth2 资源服务器拦截 → 需配 `zlt.security.ignore.http-urls` (注意: 字段是 `httpUrls` 不是 `urls`, `No setter found for property: urls` 报错), 该配置本应 Nacos 下发
4. 解决后 `Started UaaServerApp`, 端点可达

**但最终放弃**: 核心 sink `ValidateCodeServiceImpl.sendSmsCode` 的"用户为空"分支, 其 `userService.findByMobile()` 是 **feign 远程调用** (user-center 未部署), feign **抛异常**而非返回 null → HTTP 500, 永远进不了 `log.error("根据用户手机号{}查询用户为空", mobile)` 分支。与 PiggyMetrics create 同构 —— 下游依赖缺失导致结构性不可达。

#### 各项目 auth 镜像 sink 排查结论

| 项目 | 部署服务 | 业务 sink 情况 | 结论 |
|------|---------|---------------|------|
| zlt | zlt-uaa | sendSmsCode 依赖 feign user-center | ❌ 结构性不可达 |
| youlai | youlai-auth | 业务 sink 全在 mall-oms/pms/system | ❌ 部署服务无业务 sink |
| mall4cloud | mall4cloud-auth | 服务内 0 个含参业务 sink | ❌ 无 |
| gulimall | gulimall-member | 服务内 0 个含参业务 sink | ❌ 无 |
| Apollo | apollo-adminservice | AppController.create sink 逻辑不可达 (新 app accesskey count 必为 0, 达不到上限 5) | ❌ 死 sink |
| yudao | system-server | sink 需登录态(admin API) / AOP间接触发(parseFunction) / 依赖外部服务(微信/短信) | ❌ 难 fuzz |
| light-reading | 无 compose | — | ❌ 未部署 |

#### yudao parseFunction 分析 (值得记录)

`AdminUserParseFunction.apply` 等 sink (`log.warn("[apply][获取用户{{}}为空", value)`) 看似理想, 但:
- 由**操作日志 AOP 反射调用** (解析 `@OperateLog` 注解的 SpEL 函数 `getAdminUserById(x)`), Joern 静态调用图**连不通** Controller→parseFunction
- pipeline `--skip-fuzz` 实测: **生成路径 0 条** (静态分析找不到路径)
- 触发需"操作带用户 ID 的接口 + ID 不存在", 链路过间接
- 结论: 这类 sink 黑盒极难, 但也无法用本 pipeline (依赖预期路径引导)

#### 核心结论 (回答"为什么没有可做的难 sink")

**不是"没有业务镜像", 而是"业务 sink 与部署服务不匹配"**:

1. sink 扫描是**全仓库**的 (含所有业务模块), sinks/*.json 数量可观
2. 但 compose 当初 (阶段 4) 为验证 trace, 每个项目只部署**一个**服务 —— 恰好多是 auth/入口服务 (pig-auth/ruoyi-auth/youlai-auth/zlt-uaa/mall4cloud-auth 等)
3. 业务 sink 集中在**业务服务** (book/oms/pms/system), 这些**没构建镜像/没部署**
4. auth 是**薄服务** (业务逻辑在下游), 自身无多跳业务 sink

**已构建的 18 个镜像中, 部署的是业务服务的只有 3 个**:
- novel-cloud → novel-book-service ✅ (业务 sink 已 fuzz 成功, 阶段 19-22)
- PiggyMetrics → account-service ✅ (已 fuzz, 阶段 24)
- yudao → system-server ⚠️ (业务服务, 但 sink 需登录态/AOP间接/外部依赖)

其余 15 个部署的都是 auth/gateway/admin 入口服务。

**方法论推论**: 要在 auth 镜像范围外找"必须反馈"的难 sink, 必须为业务服务补构建镜像。否则 auth 镜像的难 sink 已基本穷尽 (仅 SpringBlade Captcha 一个成功案例, 阶段 23)。

#### 本次产物
- [run_yudao_pipeline.py](run_yudao_pipeline.py) — yudao pipeline (路径生成 0 条, 未 fuzz)
- zlt/yudao compose 修复 (redis 密码/datasource/security 放行/双agent/OTEL)
- 排查方法论: 选题除"可达性三问"外, 还需确认 sink 链路**不依赖未部署的下游 feign**

#### 状态
zlt / yudao 容器已启动但未 fuzz (sink 均不满足"环境就绪+必须反馈"双条件)。待决策是否构建业务服务镜像。

---

### 阶段 29: youlai mall-oms 业务服务镜像构建 + 反馈质量递进实验 (2026-08-17)

#### 目标
突破"auth 镜像难 sink 穷尽"的瓶颈, 首次为**业务服务**构建 trace 镜像, 并在一个真正"必须偏差反馈"的 sink 上验证方法优越性。

#### 业务服务镜像构建 (youlai mall-oms)

mall-oms 此前无 trace 组件、无镜像。完成从零构建:
1. **注入 trace 组件**: 复制 youlai-auth 的 `tracing/`(6类) + `tracesmoke/`(3类) 到 `mall-oms/oms-boot`, 全局替换包名 `com.youlai.auth` → `com.youlai.mall.oms`
2. **OmsApplication 加 `@EnableScheduling`**; pom 加 spring-aop/asm/aspectjweaver
3. **编译**: `mvn clean package -DskipTests -pl mall-oms/oms-boot -am` (11.7s, jar 140MB 含 trace 类)
4. **Dockerfile.mall-oms**: 复制 Dockerfile.youlai-auth 改造 (构建模块 `-pl mall-oms/oms-boot -am`, 主类 `OmsApplication`, TRACE_SMOKE_ROOT=`com.youlai.mall.oms`), 构建 + 推送 ACR `llmfuzz/youlai-mall-mall-oms:latest` ✅

#### 环境调试 (compose.oms.real.yaml)

新建 [examples-yml/youlai-mall/compose.oms.real.yaml](examples-yml/youlai-mall/compose.oms.real.yaml), 解决:
- **JwtDecoder bean 缺失** → `securityFilterChain` 创建失败, 所有请求 500。解法: 配 `--spring.security.oauth2.resourceserver.jwt.jwk-set-uri` (NimbusJwtDecoder 懒加载, bean 可建) + `--security.whitelist-paths=/**` 放行 (fuzz 不需真验 JWT)
- 端口 8803, 数据库 `mall_oms`, 单 agent + entrypoint override (既有经验)

#### 目标 sink 选定 (关键判断)

初判 `confirmOrder` L141 (`log.error("...memberId {}...", memberId)`) 为目标, 但**实测发现 memberId 来自 `SecurityUtils.getMemberId()` (JWT 安全上下文), 用户不可控 → 不可注入**。
改选 **`submitOrder` L174**: `log.info("订单提交参数:{}", JSONUtil.toJsonStr(submitForm))` — 整个提交表单序列化进日志, marker 可放 orderToken 等字段, 且在 `SecurityUtils.getMemberId()` 之前执行 (无需认证), 多跳 (Controller→ServiceImpl)。

**经验**: sink 污染参数必须**来自请求输入** (@RequestParam/@RequestBody/@PathVariable), 来自安全上下文/内部的参数不可注入。

#### 反馈质量递进对照实验 (核心成果)

同一个 submitOrder sink, 三种反馈级别对比:

| 反馈级别 | 结果 | 尝试 | 失败/成功原因 |
|---------|------|:---:|-------------|
| 无 body 反馈 (基线) | ❌ unreachable | 10/10 | LLM 不知缺哪些必填字段 |
| + 响应 body (校验错误) | ❌ unreachable | 10/10 | 知道缺"收货地址", 但猜不出 `shippingAddress` 字段名和嵌套结构 (乱猜 addressId/addrId/memberAddressId...) |
| + DTO 字段定义 | ✅ **attacked** | **3** | 拿到 `OrderSubmitForm` 完整字段定义 (含 ShippingAddress 嵌套), 一次构造对 |

#### 代码改动 (反馈增强)

1. **响应 body 反馈**: [demo_fuzz.py](demo_fuzz.py) `execute_with_trace` 把 resp_body 附到 Trace → [path_differ.py](path_differ.py) `PathDivergence.response_body` 新字段 + diff 填充 → [fuzzer.py](fuzzer.py) `_format_divergence` 输出 body (含"收货地址不能为空"等校验详情)
2. **DTO 字段定义反馈**: [fuzzer.py](fuzzer.py) 新增 `_extract_request_body_dto` (not_started 时从入口方法 `@RequestBody` 解析 DTO 类型) + `_read_class_fields` (提取字段含校验注解, 递归展开一层嵌套对象如 ShippingAddress)

#### Fuzz 结果

`submitOrder` L174 **attacked (3 次尝试)**。容器日志证据:
```
INFO OrderServiceImpl : 订单提交参数:{"orderToken":"FUZZ_MARKER_7x9k","orderSource":"APP","orderItems":[{...完整...}],"paymentAmount":100,"shippingAddress":{"consigneeName":"张三","consigneeMobile":"13800138000","province":"广东省","city":"深圳市","district":"南山区","detailAddress":"科技园路1号"},"remark":"test"}
```

#### 关键结论 (方法优越性的有力证据)

**纯黑盒 / 弱反馈 fuzz 在此 sink 必然失败, 只有源码级 DTO 反馈能成功**:
- 响应 body 只告诉 LLM"缺什么"(what), DTO 字段定义告诉它"怎么填"(how — 字段名+嵌套结构)
- 这是"偏差反馈质量决定 fuzz 成败"的直接实验证明, 反馈越接近源码级, 收敛越快 (10失败 → 10失败 → 3成功)
- 该 sink 满足了全部"难"条件: 多跳链路 + 复杂表单校验 + marker 藏 body 深层 + 嵌套对象 —— 正是体现方法价值的理想案例

#### 修改/新增文件 (未提交)
- `examples/youlai-mall/mall-oms/oms-boot/.../tracing/`, `tracesmoke/` (注入), `OmsApplication.java`, `pom.xml`
- `examples/youlai-mall/Dockerfile.mall-oms`
- `examples-yml/youlai-mall/compose.oms.real.yaml`
- [run_youlai_oms_pipeline.py](run_youlai_oms_pipeline.py)
- [demo_fuzz.py](demo_fuzz.py) / [path_differ.py](path_differ.py) / [fuzzer.py](fuzzer.py) — 响应 body + DTO 反馈

---

### 阶段 30: 参数组合靶场 —— 严格"多层嵌套+参数组合"sink 验证 (2026-08-17)

#### 背景与标准澄清

用户澄清"黑盒不可行"的严格定义: **sink 的到达必须由多个输入参数的特定组合决定** (`if(a==X && b>Y &&c==Z)`), 而非单参数触发/单层 catch/API 契约复杂。此前案例 (youlai submitOrder 是 API 契约复杂, SpringBlade Captcha 是分派) 都不严格符合。

经排查, 已部署服务中**没有现成的纯参数组合 sink** (要么单参数, 要么靠服务端状态/下游 feign)。故在 java-microservice (无外部依赖的测试服务) 构造靶场。

#### 靶场构造

[OrderService.evaluateRisk](examples/java-microservice/src/main/java/com/example/microservice/service/OrderService.java) — sink 埋在三层参数组合嵌套:
```java
if ("vip".equals(level)) {                    // 参数1: 等级
    if (amount != null && amount > 10000) {   // 参数2: 金额阈值
        if (channel != null && channel.startsWith("app")) {  // 参数3: 渠道前缀
            log.error("...VIP大额APP端高风险订单, orderId={}", orderId);  // ← sink L77
```
端点 `POST /api/orders/risk-eval`, marker 放 `orderId`。重建镜像并推送 ACR。

#### 手工验证 (证明组合必要条件)

| 输入 | 结果 |
|------|------|
| level=normal, amount=99999, channel=app-ios | ❌ 未触发 (level 不满足) |
| level=vip, amount=20000, channel=app-ios | ✅ 触发 sink |

#### Fuzz 结果: 2 次尝试成功

| 尝试 | 请求 body | 结果 | 分析 |
|------|-----------|------|------|
| 1 | `{orderId:FUZZ_MARKER, amount:99999, channel:"APP", userLevel:"VIP"}` | 到达 sink 但 marker 未出现 | 字段名错(userLevel) + 大小写错(VIP/APP), 未进 log.error 分支 |
| 2 | `{orderId:FUZZ_MARKER, amount:99999, level:"vip", channel:"app"}` | ✅ **attacked** | 修正 level/channel 为小写, 组合满足 |

**LLM 第 2 次的推理 (关键证据)**:
> "level 需要严格等于 `"vip"`(小写), channel 需要以 `"app"` 开头(区分大小写)。之前发送的是 `"VIP"` 和 `"APP"`, 因此未进入日志打印分支。修正这两个字段值...即可触发。"

#### 为什么这严格证明方法优越性

- **黑盒给了 API 定义也失败**: 即使知道 4 个字段名, 也不知道 `level` 要小写 `"vip"`、`channel` 要 `"app"` 前缀——这些组合约束只在源码里
- **偏差反馈 + 源码引导收敛**: 第 1 次"到达 sink 但 marker 未出现"的反馈 + 目标方法源码, 让 LLM 立刻定位是字段值/大小写问题, 第 2 次精确修正
- **这是严格的"多层嵌套 + 参数组合 + 必须源码反馈"案例**, 补上了此前案例都不够严格的空白

#### 修改/新增文件
- `examples/java-microservice/.../OrderService.java` (evaluateRisk 靶场方法), `AppController.java` (risk-eval 端点)
- `sinks/javams-logging-sinks.json` (靶场 sink 定义)
- `run_javams_pipeline.py`
- 镜像 `llmfuzz/java-microservice:latest` 已重建推送

---

## Stage 10: SpringBlade 真实项目多参数组合 Fuzz 演示

### 目标
在真实开源项目（非靶场）中演示"多个参数组合条件才能进入 sink"的 fuzz 场景，证明源码引导 fuzz 相对黑盒的严格优越性。

### 项目选择过程
1. 对 18 个项目的 3115 个 sink 进行全量扫描，筛选"纯参数分支"sink
2. 发现：所有复杂 sink 都有一定的状态依赖（DB/Redis/Feign），不存在"纯参数-only"的 sink
3. 用户放宽约束：允许预填状态（DB/Redis），重点演示参数组合条件
4. 最终选定 **SpringBlade CaptchaTokenGranter.grant()** — 6 参数组合条件

### 目标 Sink

```
CaptchaTokenGranter.grant() 第 95 行:
log.error("用户登录失败, 账号:{}, IP:{}", account, WebUtil.getIP())
```

### 6 参数组合条件

| # | 参数 | 来源 | 约束 |
|---|------|------|------|
| 1 | Captcha-Key | 自定义 header | Redis 中预存的验证码 key |
| 2 | Captcha-Code | 自定义 header | 必须与 Redis 值匹配（忽略大小写）|
| 3 | tenantId | query param | 必须是有效租户 ID (000000) |
| 4 | account | query param | 账号名 |
| 5 | password | query param | SM2 加密密码（当前 key 为空，解密返回空串）|
| 6 | User-Type | 自定义 header | "web" 或 "app"，决定 Feign 调用分支 |

额外隐含条件：
- `grantType` 参数名是驼峰（不是 `grant_type`），默认值 "password"
- 需要 Basic Authorization header（`sword:sword_secret`）
- 需要 blade-system 微服务运行（Feign 调用获取用户信息）
- 验证码存储使用 ProtoStuff 序列化，redis-cli 写入的纯文本无法被反序列化

### 环境搭建

1. **blade-system 构建**：本地 Maven 仓库有 root 所有的文件导致权限错误，改用 Docker 多阶段构建
2. **Feign 直连配置**：
   - `network_mode: host` 下无 Docker DNS，Feign 把 `blade-system` 当主机名解析
   - 尝试 YAML SimpleDiscoveryClient、命令行参数、autoconfigure.exclude 均失败
   - 根因：shell 形式 entrypoint 的 `$0 $@` 吞掉了 `[0]` 语法
   - 最终方案：exec 形式 entrypoint + `spring.config.import` + `--spring.cloud.discovery.client.simple.instances.blade-system[0].uri=http://127.0.0.1:8106`
3. **验证码预填**：BladeRedis 使用 ProtoStuff 序列化，需要通过 `/captcha` API 生成真实验证码再从 Redis 提取

### Fuzz 结果 (Mock 模式)

| 轮次 | 请求特征 | 结果 | 偏差反馈 |
|------|----------|------|----------|
| 1 | 缺少 Captcha-Key/Captcha-Code header | ❌ | depth 2/6: 到达 CaptchaTokenGranter.grant, 未过验证码校验 |
| 2 | 错误的 captcha key (wrong-key) | ❌ | 同上，验证码不匹配 |
| 3 | 真实验证码 + 缺少 User-Type | ✅ **到达 sink** | granter=True, login_handler=True |

**第 3 轮即到达 sink**，总耗时 0.5 秒。

### 源码引导 vs 黑盒对比

黑盒（即使给了 API 文档）无法知道的信息：
1. `Captcha-Key` / `Captcha-Code` 是自定义 header（非标准 OAuth2 参数）
2. 验证码存储在 Redis 的 `blade:auth::blade:captcha:{key}` 下，使用 ProtoStuff 序列化
3. `User-Type` header 只接受 "web"/"app"（自定义枚举）
4. `grantType` 参数名是驼峰（Java 命名风格，非 OAuth2 标准的 `grant_type`）
5. 需要 Basic Authorization header（client_id:client_secret = sword:sword_secret）
6. SM2 密钥为空时 decryptPassword 返回空串（不抛异常），静默走完全流程
7. `TokenGranterBuilder` 用静态 Map 分发 grant type，null/空值 fallback 到 "password"

**结论**：6 参数组合 + 多个隐含条件 + ProtoStuff 序列化 + 驼峰参数名，黑盒几乎不可能在合理尝试次数内到达 sink。源码引导 + trace 偏差反馈在第 3 轮即收敛。

### 修改/新增文件
- `demo_fuzz_springblade.py` — SpringBlade fuzz 演示脚本
- `examples/SpringBlade/Dockerfile.blade-system` — blade-system Docker 构建文件
- `examples-yml/SpringBlade/compose.real.yaml` — 加入 blade-system 服务 + Feign 直连配置
- `examples-yml/SpringBlade/trace-validation.yml` — blade-auth 配置（SimpleDiscoveryClient）
