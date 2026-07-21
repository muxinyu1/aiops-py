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
