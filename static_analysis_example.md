# 静态分析流水线实战记录：java-microservice

> 以 `examples/java-microservice` 项目为例，完整记录从源码到预期路径的每一步操作和产物。

## 项目概况

| 属性 | 值 |
|------|---|
| 项目名 | java-microservice |
| 语言 | Java 17 + Spring Boot |
| 源码位置 | `examples/java-microservice/src/main/java/com/example/microservice/` |
| 包名 | `com.example.microservice` |
| 构建工具 | Maven |
| 模块结构 | controller / service / model / tracing |

## 第一步：Joern 生成调用图

### 输入
- Java 源码目录：`examples/java-microservice/src/main/java`

### 执行命令

```bash
# 1. 解析源码生成 CPG (Code Property Graph)
~/bin/joern/joern-cli/joern-parse \
  examples/java-microservice/src/main/java \
  --language javasrc \
  -o /tmp/java-microservice.cpg

# 2. 运行导出脚本提取调用图
~/bin/joern/joern-cli/joern \
  --script /tmp/export_cg.sc \
  --params cpgFile=/tmp/java-microservice.cpg,outFile=/tmp/java-microservice-cg.json
```

### 耗时
- CPG 生成：~8s
- 调用图导出：~5s
- 总计：~13s

### 产物：`/tmp/java-microservice-cg.json`

```json
{
  "methods": [
    {
      "fullName": "com.example.microservice.controller.AppController.getUser",
      "name": "getUser",
      "filename": "AppController.java",
      "lineNumber": 25
    },
    {
      "fullName": "com.example.microservice.service.UserService.findById",
      "name": "findById",
      "filename": "UserService.java",
      "lineNumber": 18
    },
    ...
  ],
  "calls": [
    {
      "caller": "com.example.microservice.controller.AppController.getUser",
      "callee": "com.example.microservice.service.UserService.findById"
    },
    {
      "caller": "com.example.microservice.controller.AppController.createUser",
      "callee": "com.example.microservice.service.UserService.create"
    },
    ...
  ]
}
```

### 统计

| 指标 | 值 |
|------|---|
| 方法节点 | 68 |
| 调用边 | 38 |
| Controller 方法 | 8 |
| Service 方法 | 9 |
| Tracing 方法 | 20+ |

---

## 第二步：CodeQL 污点分析

### 输入
- Java 项目根目录（含 pom.xml）：`examples/java-microservice`
- 需要先编译项目

### 2.1 创建 CodeQL 数据库

```bash
~/bin/codeql/codeql database create /tmp/codeql-db-java-microservice \
  --language=java \
  --command="mvn compile -DskipTests -q" \
  --overwrite
```

**耗时**：~15s

**产物**：`/tmp/codeql-db-java-microservice/` 目录

```
/tmp/codeql-db-java-microservice/
├── db-java/           # 关系数据库
├── log/               # 构建日志
├── src.zip            # 源码归档
└── codeql-database.yml
```

数据库大小：
- Relations: 688.85 KiB
- String pool: 2.78 MiB

### 2.2 编写 Taint Tracking Query

文件：`/tmp/codeql-query/taint_edges.ql`

```ql
/**
 * @name Taint-carrying method call pairs
 * @kind problem
 * @id aiops/taint-method-pairs
 */

import java
import semmle.code.java.dataflow.TaintTracking
import semmle.code.java.dataflow.FlowSources

module TaintConfig implements DataFlow::ConfigSig {
  predicate isSource(DataFlow::Node source) {
    source instanceof RemoteFlowSource
    or
    // Spring Controller 方法参数
    exists(Parameter p |
      p.getCallable().getDeclaringType().getAnAnnotation().getType()
        .hasQualifiedName("org.springframework.web.bind.annotation", "RestController")
      and
      p.getCallable().getAnAnnotation().getType()
        .hasQualifiedName("org.springframework.web.bind.annotation", _)
    |
      source.asParameter() = p
    )
  }

  predicate isSink(DataFlow::Node sink) {
    exists(MethodCall mc |
      mc.getMethod().getCompilationUnit().fromSource() |
      sink.asExpr() = mc.getAnArgument()
    )
  }
}

module TaintFlow = TaintTracking::Global<TaintConfig>;

from MethodCall call, Method caller, Method callee, DataFlow::Node src, DataFlow::Node snk
where
  TaintFlow::flow(src, snk) and
  snk.asExpr() = call.getAnArgument() and
  call.getEnclosingCallable() = caller and
  call.getMethod() = callee and
  caller.getCompilationUnit().fromSource() and
  callee.getCompilationUnit().fromSource()
select call,
  caller.getDeclaringType().getQualifiedName() + "." + caller.getName() + "|" +
  callee.getDeclaringType().getQualifiedName() + "." + callee.getName()
```

核心逻辑：
- **Source**：HTTP 请求参数（RemoteFlowSource）+ Spring Controller 方法参数
- **Sink**：用户代码中的方法调用参数
- **输出**：每个有 taint 流的 `caller|callee` 对

### 2.3 执行查询

```bash
# 安装依赖
~/bin/codeql/codeql pack install /tmp/codeql-query

# 运行查询
~/bin/codeql/codeql query run /tmp/codeql-query/taint_edges.ql \
  --database=/tmp/codeql-db-java-microservice \
  --output=/tmp/codeql-results.bqrs

# 解码为 CSV
~/bin/codeql/codeql bqrs decode /tmp/codeql-results.bqrs \
  --format=csv \
  --output=/tmp/codeql-taint-results.csv
```

**耗时**：
- 编译查询：1m3s（首次，后续有缓存）
- 评估：4.9s

### 产物：`/tmp/codeql-taint-results.csv`

```csv
"call","col1"
"findById(...)","com.example.microservice.controller.AppController.getUser|com.example.microservice.service.UserService.findById"
"create(...)","com.example.microservice.controller.AppController.createUser|com.example.microservice.service.UserService.create"
"success(...)","com.example.microservice.controller.AppController.createUser|com.example.microservice.model.ApiResponse.success"
"findById(...)","com.example.microservice.controller.AppController.getOrder|com.example.microservice.service.OrderService.findById"
"cancel(...)","com.example.microservice.controller.AppController.cancelOrder|com.example.microservice.service.OrderService.cancel"
"success(...)","com.example.microservice.controller.AppController.cancelOrder|com.example.microservice.model.ApiResponse.success"
"extractTraceId(...)","com.example.microservice.tracing.TraceFilter.doFilterInternal|com.example.microservice.tracing.TraceFilter.extractTraceId"
"set(...)","com.example.microservice.tracing.TraceFilter.doFilterInternal|com.example.microservice.tracing.TraceContextHolder.set"
"setTargets(...)","com.example.microservice.tracing.TraceFilter.doFilterInternal|com.example.microservice.tracing.SnapshotTargetRegistry.setTargets"
"getAndRemove(...)","com.example.microservice.tracing.TraceFilter.doFilterInternal|com.example.microservice.tracing.TraceStore.getAndRemove"
"add(...)","com.example.microservice.tracing.TracingAspect.traceMethod|com.example.microservice.tracing.TraceStore.add"
```

### 统计

| 指标 | 值 |
|------|---|
| 总 taint 对 | 11 |
| Controller → Service | 5 |
| Controller → Model | 2 |
| Filter → Tracing | 4 |

---

## 第三步：标记调用图 Taint 边

### 输入
- Joern 调用图：68 节点, 38 边, 0 条 taint 边
- CodeQL 结果：11 条 taint 对

### 操作

```python
from joern_adapter import load_call_graph_from_json
from codeql_adapter import CodeQLAdapter, CodeQLConfig

# 加载调用图
cg = load_call_graph_from_json("/tmp/java-microservice-cg.json")

# 标记 taint
adapter = CodeQLAdapter(config=CodeQLConfig(codeql_home="~/bin/codeql"))
taint_result = adapter.mark_taint_edges_from_results(cg, "/tmp/codeql-taint-results.csv")
```

### 匹配逻辑
CodeQL 输出的 `caller|callee` 与 Joern 调用图的 `edge.caller_id` / `edge.callee_id` 直接字符串匹配（都是 `com.package.Class.method` 格式）。

### 产物：标记后的调用图

| 指标 | 值 |
|------|---|
| 总边数 | 38 |
| 已标记 taint 边 | 11 |
| 匹配率 | 11/11 = 100% |
| 未匹配 | 0 |

被标记的边：

| # | Caller | Callee |
|---|--------|--------|
| 1 | AppController.getUser | UserService.findById |
| 2 | AppController.createUser | UserService.create |
| 3 | AppController.createUser | ApiResponse.success |
| 4 | AppController.getOrder | OrderService.findById |
| 5 | AppController.cancelOrder | OrderService.cancel |
| 6 | AppController.cancelOrder | ApiResponse.success |
| 7 | TraceFilter.doFilterInternal | TraceFilter.extractTraceId |
| 8 | TraceFilter.doFilterInternal | TraceContextHolder.set |
| 9 | TraceFilter.doFilterInternal | SnapshotTargetRegistry.setTargets |
| 10 | TraceFilter.doFilterInternal | TraceStore.getAndRemove |
| 11 | TracingAspect.traceMethod | TraceStore.add |

---

## 第四步：PathGenerator 生成预期路径

### 输入
- 调用图：68 节点, 38 边 (其中 11 条 taint)
- API 入口：5 个 Controller 方法
- Sink 目标：6 个 Service/Store 方法

### API 入口定义

| HTTP Method | Path | Class.Method |
|------------|------|-------------|
| GET | /api/users/{id} | AppController.getUser |
| POST | /api/users | AppController.createUser |
| GET | /api/orders/{id} | AppController.getOrder |
| POST | /api/orders | AppController.createOrder |
| POST | /api/orders/{id}/cancel | AppController.cancelOrder |

### Sink 目标定义

| Class | Method | Level |
|-------|--------|-------|
| UserService | findById | INFO |
| UserService | create | INFO |
| OrderService | findById | INFO |
| OrderService | create | INFO |
| OrderService | cancel | INFO |
| TraceStore | add | DEBUG |

### 算法
BFS 从每个 API 入口出发，在调用图上搜索可达的 Sink 方法。对每个 (API, Sink) 对选择最优路径：
- 优先级：Taint 路径 > CG-only 路径
- 同等情况下选短路径

### 产物：5 条预期路径

| # | 类型 | Confidence | 入口 | Sink | 路径 |
|---|------|-----------|------|------|------|
| 1 | 🔴 TAINT | 0.80 | GET /api/users/{id} | UserService.findById | AppController.getUser → UserService.findById |
| 2 | 🔴 TAINT | 0.80 | POST /api/users | UserService.create | AppController.createUser → UserService.create |
| 3 | 🔴 TAINT | 0.80 | GET /api/orders/{id} | OrderService.findById | AppController.getOrder → OrderService.findById |
| 4 | ⚪ CG | 0.50 | POST /api/orders | OrderService.create | AppController.createOrder → OrderService.create |
| 5 | 🔴 TAINT | 0.80 | POST /api/orders/{id}/cancel | OrderService.cancel | AppController.cancelOrder → OrderService.cancel |

### 为什么路径 4 是 CG-only？
`createOrder` 接收 `@RequestBody OrderRequest body`，CodeQL 的默认 RemoteFlowSource 不包含 `@RequestBody` 注解参数（需要 Models-as-Data 扩展配置）。所以虽然调用关系存在，但 CodeQL 没有证实有 taint 流经过这条边。

---

## 第五步：使用预期路径验证实际 Trace

### 输入
- 预期路径集（上一步产物）
- 实际运行时 trace（通过 `X-Return-Trace: true` 请求头采集）

### 示例：验证 GET /api/users/1

**发送请求：**
```bash
curl -H "X-Return-Trace: true" http://localhost:8080/api/users/1
```

**响应中的 trace (Base64 解码后)：**
```json
[
  {"class": "com.example.microservice.tracing.TraceFilter", "method": "doFilterInternal", "ts": 1721433600001},
  {"class": "com.example.microservice.controller.AppController", "method": "getUser", "ts": 1721433600005},
  {"class": "com.example.microservice.service.UserService", "method": "findById", "ts": 1721433600008},
  {"class": "com.example.microservice.repository.UserRepository", "method": "findById", "ts": 1721433600012}
]
```

**预期路径 #1：**
```
AppController.getUser → UserService.findById
```

**验证逻辑：**
```
预期路径中的节点: {AppController.getUser, UserService.findById}
实际 trace 中的节点: {TraceFilter.doFilterInternal, AppController.getUser, UserService.findById, UserRepository.findById}

预期 ⊆ 实际 → ✅ 验证通过
```

### 验证结果矩阵

| API | 预期路径存在 | Trace 覆盖 | 结果 |
|-----|------------|-----------|------|
| GET /api/users/{id} | ✅ | ✅ | PASS |
| POST /api/users | ✅ | ✅ | PASS |
| GET /api/orders/{id} | ✅ | ✅ | PASS |
| POST /api/orders | ✅ | ✅ | PASS |
| POST /api/orders/{id}/cancel | ✅ | ✅ | PASS |

覆盖率：5/5 = **100%**

---

## 全流程产物汇总

| 步骤 | 产物 | 位置 | 大小 |
|------|------|------|------|
| 1. Joern CPG | CPG 二进制文件 | `/tmp/java-microservice.cpg` | ~5MB |
| 1. Joern CG | 调用图 JSON | `/tmp/java-microservice-cg.json` | ~12KB |
| 2. CodeQL DB | 数据库目录 | `/tmp/codeql-db-java-microservice/` | ~4MB |
| 2. CodeQL Query | Taint 查询文件 | `/tmp/codeql-query/taint_edges.ql` | ~2KB |
| 2. CodeQL Result | Taint 结果 CSV | `/tmp/codeql-taint-results.csv` | ~1KB |
| 3. 标记后 CG | 内存中 CallGraph 对象 | — | 68节点/38边/11taint |
| 4. 预期路径 | 内存中 ExpectedPathSet | — | 5条路径 |

## 全流程耗时

| 步骤 | 耗时 | 备注 |
|------|------|------|
| Joern CPG 生成 | ~8s | 首次；增量可更快 |
| Joern CG 导出 | ~5s | |
| CodeQL DB 创建 | ~15s | 含 Maven 编译 |
| CodeQL Query 编译 | ~63s | 首次；后续有缓存 |
| CodeQL Query 评估 | ~5s | |
| Taint 边标记 | <1ms | 纯内存操作 |
| PathGenerator BFS | <1ms | 小规模图 |
| **总计** | **~96s** | 首次完整运行 |
| **使用缓存** | **<10ms** | 跳过 Joern + CodeQL |

---

## 一键复现

```bash
# 前提: 已安装 Joern (~/bin/joern) + CodeQL (~/bin/codeql) + JDK 21 + Maven

# 完整运行
python3 example_static_analysis.py --mode full

# 或使用缓存 (如果已有 /tmp 下的产物)
python3 example_static_analysis.py --mode cache
```
