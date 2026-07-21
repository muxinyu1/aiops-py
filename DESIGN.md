# AIOps-Py 项目设计文档

## 1. 项目概述

基于 GONDAR 论文思想的 **Sink-Centric Fuzzing 框架**，面向 Java 微服务系统。

核心目标：给定一个微服务应用的 API 入口（Source）和日志打印点（Sink），通过 LLM 引导的模糊测试，生成能到达 Sink 的请求参数，并在执行偏离预期路径时通过偏差反馈指导下一轮 Fuzz。

## 2. 系统架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        Pipeline (主控循环)                        │
│                                                                 │
│   ┌─────────┐     ┌──────────┐     ┌────────────┐     ┌──────┐ │
│   │ Fuzzer  │────▶│ Executor │────▶│ PathDiffer │────▶│ LLM  │ │
│   │(生成参数)│◀────│(发送请求) │     │ (偏差计算)  │     │(反馈) │ │
│   └─────────┘     └──────────┘     └────────────┘     └──────┘ │
│        ▲                                   │                    │
│        └───────────── 偏差反馈 ─────────────┘                    │
└─────────────────────────────────────────────────────────────────┘
                              │
                    ┌─────────▼─────────┐
                    │   目标微服务容器     │
                    │  (Docker + trace)  │
                    │                   │
                    │  TracingAspect    │ ← AOP 方法级 trace
                    │  trace-agent     │ ← Byte Buddy 字节码插桩
                    │  JaCoCo agent    │ ← 行级覆盖率
                    └───────────────────┘
```

## 3. 核心模块

### 3.1 数据模型

| 模块 | 职责 |
|------|------|
| `source.py` | 请求入口描述（RESTful / RPC）|
| `sink.py` | 日志打印点 / 安全敏感 API 调用点 |
| `parameter.py` | HTTP 请求参数（method, path, headers, body）|
| `trace.py` | 完整请求追踪模型（TraceNode 调用树 + CoverageData 行级覆盖）|
| `difference.py` | 两个 Trace 间的基本块执行流偏差（DivergencePoint）|
| `expected_path.py` | 静态预期路径模型（PathNode, APIEntry, LogSink, ExpectedPath）|

### 3.2 执行路径分析

| 模块 | 职责 |
|------|------|
| `path_generator.py` | 从调用图生成 API×Sink 预期路径矩阵（BFS，优先污点路径）|
| `path_differ.py` | 实际 Trace vs 静态 ExpectedPath 的偏差比较，定位第一个断裂点 |
| `differ.py` | 两个动态 Trace 之间的偏差（调用树 DFS + JaCoCo 行级覆盖）|

### 3.3 Fuzz 循环

| 模块 | 职责 |
|------|------|
| `fuzzer.py` | LLM 驱动的参数生成器，根据偏差反馈调整 fuzz 策略 |
| `executor.py` | 请求执行器，发送 HTTP 请求并收集 trace + coverage |
| `pipeline.py` | 主控循环：fuzz → execute → diff → feedback → fuzz |
| `llm.py` | LLM 接口封装 |

### 3.4 基础设施

| 模块 | 职责 |
|------|------|
| `docker.py` | Docker Compose 管理（启停容器、健康检查）|
| `jacoco_client.py` | JaCoCo TCP 协议交互（per-request reset/dump/report）|
| `otlp_receiver.py` | OpenTelemetry span 接收 |
| `batch_trace_integration.py` | 批量向目标项目注入 tracing 代码并构建 Docker 镜像 |
| `validate_trace_images.py` | 验证构建好的镜像能正确返回 trace |

## 4. Tracing 机制

### 4.1 双层 Trace 采集

每次 HTTP 请求返回两条追踪数据（通过响应头）：

1. **X-Execution-Trace**：方法级调用栈（Base64 JSON 数组）
   - 来源：`TracingAspect`（Spring AOP）+ `trace-agent`（Byte Buddy）
   - 内容：span_id, parent_span_id, class_namespace, function, duration_ns, is_error 等
   
2. **X-Coverage-Data**：行级覆盖率（JaCoCo .exec 二进制）
   - 来源：JaCoCo agent（TCP server 模式，per-request reset）
   - 内容：每个类中被执行的源码行号

### 4.2 trace-agent（Byte Buddy）

位置：`trace-agent/`

- `AgentMain.java`：入口，配置 Byte Buddy instrumentation，注入 SpanStackHelper 到 bootstrap classloader
- `MethodTraceAdvice.java`：@OnMethodEnter/@OnMethodExit，记录方法进出
- `SpanStackHelper.java`：ThreadLocal span 栈，维护 parent-child 关系，通过反射委托给应用的 TraceContextHolder

### 4.3 应用内 Tracing 组件（注入到每个目标项目）

- `TraceContextHolder.java`：ThreadLocal traceId + span 栈（pushSpan/popSpan/currentParentSpanId）
- `TracingAspect.java`：Spring AOP @Around，拦截 controller/service 层方法
- `TraceFilter.java`：Servlet Filter，触发 trace 采集（X-Return-Trace: true）
- `TraceStore.java`：收集当前请求所有 span，返回前序列化到响应头
- `SpanRecord.java`：单个 span 的数据结构
- `LineNumberResolver.java`：从字节码解析方法行号

### 4.4 触发方式

请求头带 `X-Return-Trace: true` → TraceFilter 设置 traceId → TracingAspect 和 trace-agent 采集 span → 响应头返回 trace 数据。

## 5. 预期路径系统（GONDAR 启发）

### 5.1 设计思路

参考 GONDAR 的 Reachability Progress Analysis：
- 静态分析生成从每个 API 入口到每个 Sink 的**预期调用路径**
- 运行时将实际 Trace 与预期路径对比，找到第一个**断裂点**
- 断裂点信息作为反馈提供给 LLM，指导生成更有针对性的 fuzz 参数

### 5.2 路径生成（path_generator.py）

输入：调用图（CallGraph）+ API 入口列表 + Sink 列表

算法：
1. 对每个 API 入口做 BFS
2. 找到所有可达的 Sink
3. 每个 (API, Sink) 对选择**最优路径**（污点路径 > 调用图路径，短路径 > 长路径）
4. 生成 ExpectedPathSet

### 5.3 偏差计算（path_differ.py）

输入：实际 Trace + ExpectedPath

算法：
1. **树对齐**（_align_path）：沿预期路径逐节点，在 trace 调用树中 DFS 查找匹配
2. **平铺搜索**（_align_by_flat）：忽略树结构，在所有 trace 节点中搜索匹配
3. 取两者最优结果，输出 `PathDivergence`（reached_depth, first_missed_node, reach_rate）

## 6. 目标项目

16 个 MVC 微服务项目（`examples/` 下）：

| 项目 | 模块 | 包名 |
|------|------|------|
| pig | pig-auth | com.pig4cloud.pig.auth |
| RuoYi-Cloud | ruoyi-auth | com.ruoyi.auth |
| RuoYi-Cloud-Plus | ruoyi-auth | org.dromara.auth |
| mall-swarm | mall-admin | com.macro.mall |
| SpringBlade | blade-auth | org.springblade.auth |
| youlai-mall | youlai-auth | com.youlai.auth |
| mall4cloud | mall4cloud-auth | com.mall4j.cloud.auth |
| zlt-microservices-platform | zlt-uaa | com.central |
| Apollo | apollo-adminservice | com.ctrip.framework.apollo.adminservice |
| novel-cloud | novel-book-service | io.github.xxyopen.novel.book |
| yudao-cloud | yudao-module-system-server | cn.iocoder.yudao.module.system |
| PiggyMetrics | account-service | com.piggymetrics.account |
| MoGuBlog | mogu_admin | com.moxi.mogublog.admin |
| Cloud-Platform | ace-admin | com.github.wxiaoqi.security |
| PassJava-Platform | passjava-member | com.jackson0714.passjava.member |
| gulimall-learning | gulimall-member | io.niceseason.gulimall.member |
| lamp-cloud | lamp-oauth-server | top.tangyh.lamp |
| java-microservice | — | com.example.microservice |

Docker Registry：`crpi-8tnv6lve87c20oxm.cn-beijing.personal.cr.aliyuncs.com/llmfuzz/`

## 7. 已完成工作

### 7.1 预期路径系统（完整实现）

- [x] `expected_path.py` — 数据模型（PathNode, LogSink, APIEntry, ExpectedPath, ExpectedPathSet）
- [x] `path_generator.py` — BFS 路径生成（CallGraph → ExpectedPathSet）
- [x] `path_differ.py` — Trace vs ExpectedPath 偏差计算（树 + 平铺双策略）
- [x] `tests/test_path.py` — 17 个单元测试全部通过

### 7.2 parent_span_id Bug 修复

**问题**：TracingAspect 将 parent_span_id 硬编码为空字符串，导致 trace 无法构建调用树。

**修复**：
- TraceContextHolder 新增 ThreadLocal span 栈（pushSpan/popSpan/currentParentSpanId）
- TracingAspect 在 proceed() 前后 push/pop span，正确记录父子关系
- trace-agent 的 SpanStackHelper 通过反射委托给应用栈，两者共享
- 所有 16 个项目源码已修复
- `batch_trace_integration.py` 模板已更新（不会再生成有 bug 的代码）
- PiggyMetrics E2E 验证通过

### 7.3 基础设施

- [x] trace-agent 编译完成（`trace-agent/target/trace-agent-1.0.0.jar`）
- [x] Docker Compose 编排（`examples-yml/` 下每个项目有 compose.real.yaml）
- [x] JaCoCo 集成（per-request 覆盖率采集）
- [x] `docker.py` — 容器生命周期管理
- [x] `tests/test_docker.py` — Docker 模块单元测试
- [x] `batch_trace_integration.py` — 一键注入 tracing + 构建镜像 + 推送
- [x] 59 个单元测试全部通过

### 7.4 Differ 系统

- [x] `differ.py` — Trace vs Trace 偏差计算（调用树 DFS + JaCoCo 行覆盖）
- [x] `tests/test_differ.py` — Differ 单元测试

## 8. 待完成工作

- [ ] `fuzzer.py` — LLM 驱动的参数生成（需要完善 prompt 工程）
- [ ] `pipeline.py` — 主控循环集成
- [ ] `executor.py` — 完善请求执行 + trace 采集的完整流程
- [ ] `llm.py` — 接入实际 LLM API
- [ ] 多项目批量 Fuzz 验证
- [ ] 全量镜像重建推送（`./build_mvc_traced_and_push.sh all`）

## 9. 运行方式

```bash
# 环境
uv sync                              # 安装依赖
cd trace-agent && mvn package -DskipTests  # 编译 trace-agent

# 单元测试
uv run pytest tests/ -v

# 构建并推送所有镜像
./build_mvc_traced_and_push.sh all

# 启动单个项目验证
cd examples-yml/PiggyMetrics && docker compose -f compose.real.yaml up -d

# 发送 trace 请求
curl -X POST http://127.0.0.1:8080/ \
  -H "Content-Type: application/json" \
  -H "X-Return-Trace: true" \
  -d '{"username":"test","password":"pass"}'

# E2E 测试
uv run python test_path_e2e.py
```