# 运行时变量快照功能使用指南

## 概述

现在偏差计算不仅能定位到分歧发生的代码位置（类、方法、行号），还能获取**偏差发生时刻的运行时变量值**（方法参数、返回值、对象字段状态），帮助理解为什么执行路径在此处发生了偏离。

## 工作原理

### 两阶段执行模式

**Phase 1: 定位偏差**
- 正常执行两次请求（trace_a vs trace_b）
- 使用 `Differ.diff()` 计算偏差
- 得到 `DivergencePoint`（类名、方法名、分歧行号）

**Phase 2: 采集变量快照**
- 根据偏差结果构造 `X-Snapshot-Methods` 请求头
- 带该头重新执行请求
- trace-agent 在目标方法处采集变量快照
- 将快照数据合并到 `DivergencePoint`

### Java 端实现

**trace-agent 增强**：
- `MethodSnapshotAdvice`: 使用 Byte Buddy 捕获方法参数、返回值、this 对象字段
- `SnapshotSerializer`: 将复杂对象序列化为 JSON (1层深度)
- `SnapshotTargetRegistry`: 运行时判断哪些方法需要快照（根据请求头）
- `SpanRecord` 扩展: 新增 `args_snapshot`, `return_snapshot`, `this_snapshot` 字段

### Python 端实现

**数据结构扩展**：
- `TraceNode`: 新增快照字段
- `VariableSnapshot`: 结构化存储参数、返回值、this 状态
- `DivergencePoint`: 持有 `snapshot_a` 和 `snapshot_b`

**SnapshotDiffer**：
- `build_snapshot_targets()`: 根据偏差构造需要快照的方法列表
- `enrich_difference()`: 将快照数据填入偏差结果

## 使用示例

### 基础用法

```python
from differ import Differ
from snapshot_executor import SnapshotDiffer, SnapshotConfig
import requests

# 假设你有一个执行函数 execute_request(params, headers)
# 它会向带 trace-agent 的微服务发起请求并返回 Trace

# Step 1: 第一阶段 - 获取偏差
params_a = {"userId": "123", "action": "validate"}
params_b = {"userId": "999", "action": "validate"}

trace_a = execute_request(params_a, headers={"X-Return-Trace": "true"})
trace_b = execute_request(params_b, headers={"X-Return-Trace": "true"})

differ = Differ()
difference = differ.diff(trace_a, trace_b)

print(f"发现偏差: {difference.has_divergence}")
if difference.first_divergence:
    dp = difference.first_divergence
    print(f"第一个分歧点: {dp.class_name} 第 {dp.diverge_line} 行")

# Step 2: 第二阶段 - 采集变量快照
snapshot_differ = SnapshotDiffer(config=SnapshotConfig(context_depth=2))

# 构造快照请求头
snapshot_headers = snapshot_differ.build_snapshot_header(difference, trace_a, trace_b)
print(f"快照请求头: {snapshot_headers}")
# → {"X-Return-Trace": "true", 
#    "X-Snapshot-Methods": "com.example.service.UserService.validate,com.example.dao.UserDao.findById"}

# 带快照头重新执行
trace_a_snapshot = execute_request(params_a, headers=snapshot_headers)
trace_b_snapshot = execute_request(params_b, headers=snapshot_headers)

# 将快照数据合并到偏差结果
enriched_diff = snapshot_differ.enrich_difference(
    difference, trace_a_snapshot, trace_b_snapshot
)

# Step 3: 查看带变量快照的偏差
print(enriched_diff.divergence_summary)
# → First divergence at UserService.java:42 (class: com.example.service.UserService). 
#   Lines only in A: [43, 44, 45], only in B: [47, 48].
#   [Trace A vars] args={"userId":"123"}, return={"type":"Boolean","value":true}, this={"maxRetries":3}
#   [Trace B vars] args={"userId":"999"}, return={"type":"Boolean","value":false}, this={"maxRetries":3}

# 访问结构化快照数据
if enriched_diff.first_divergence.snapshot_a:
    snap_a = enriched_diff.first_divergence.snapshot_a
    print(f"Trace A 参数: {snap_a.args}")
    print(f"Trace A 返回值: {snap_a.return_value}")
    print(f"Trace A this 状态: {snap_a.this_state}")
```

### 集成到 Pipeline

```python
from pipeline import Pipeline
from differ import Differ
from snapshot_executor import SnapshotDiffer, SnapshotConfig

class EnhancedPipeline(Pipeline):
    def __init__(self):
        super().__init__()
        self.differ = Differ()
        self.snapshot_differ = SnapshotDiffer(
            config=SnapshotConfig(context_depth=2)
        )
    
    def run_iteration(self, params_normal, params_fuzzy):
        # Phase 1: 计算偏差（无快照）
        trace_normal = self.executor.execute(params_normal)
        trace_fuzzy = self.executor.execute(params_fuzzy)
        
        difference = self.differ.diff(trace_normal, trace_fuzzy)
        
        if not difference.has_divergence:
            return difference
        
        # Phase 2: 采集快照
        snapshot_headers = self.snapshot_differ.build_snapshot_header(
            difference, trace_normal, trace_fuzzy
        )
        
        # 重新执行（带快照头）
        trace_normal_snap = self.executor.execute(
            params_normal, extra_headers=snapshot_headers
        )
        trace_fuzzy_snap = self.executor.execute(
            params_fuzzy, extra_headers=snapshot_headers
        )
        
        # 合并快照
        enriched_diff = self.snapshot_differ.enrich_difference(
            difference, trace_normal_snap, trace_fuzzy_snap
        )
        
        # 将带变量快照的偏差反馈给 LLM
        self.llm.generate_next_params(enriched_diff)
        
        return enriched_diff
```

### 高级配置

```python
# 自定义快照范围
config = SnapshotConfig(
    context_depth=3,           # 采集分歧方法的3层调用者
    capture_all=False,         # False = 仅采集偏差相关方法; True = 所有方法
    max_snapshot_methods=20    # 最多快照20个方法
)

snapshot_differ = SnapshotDiffer(config=config)

# 手动指定快照方法（而不是自动推断）
custom_methods = "com.example.service.UserService.validate,com.example.dao.UserDao"
headers = {
    "X-Return-Trace": "true",
    "X-Snapshot-Methods": custom_methods
}

# 全量快照（所有方法）
headers_all = {
    "X-Return-Trace": "true",
    "X-Snapshot-Methods": "*"
}
```

## X-Snapshot-Methods 头格式

支持多种格式，灵活匹配：

```
# 1. 完全限定类名.方法名
X-Snapshot-Methods: com.example.service.UserService.validate

# 2. 简单类名.方法名（匹配任何包下的该类）
X-Snapshot-Methods: UserService.validate

# 3. 仅方法名（匹配所有类中该方法）
X-Snapshot-Methods: validate

# 4. 仅类名（匹配该类所有方法）
X-Snapshot-Methods: com.example.service.UserService

# 5. 多个方法（逗号分隔）
X-Snapshot-Methods: UserService.validate,UserDao.findById,OrderService

# 6. 全量采集
X-Snapshot-Methods: *
```

## 快照数据结构

### TraceNode 快照字段

```python
@dataclass
class TraceNode:
    # ... 原有字段 ...
    
    # 运行时变量快照 (仅在 snapshot 模式下填充)
    args_snapshot: Optional[dict] = None
    # e.g. {"userId": "123", "status": "ACTIVE"}
    
    return_snapshot: Optional[dict] = None
    # e.g. {"type": "Boolean", "value": true}
    
    this_snapshot: Optional[dict] = None
    # e.g. {"_class": "UserService", "maxRetries": 3, "userDao": "..."}
```

### VariableSnapshot

```python
@dataclass
class VariableSnapshot:
    args: Optional[dict] = None          # 方法参数
    return_value: Optional[dict] = None  # 返回值
    this_state: Optional[dict] = None    # this 对象字段
    
    def has_data(self) -> bool:
        """是否包含任何快照数据"""
        
    def summary(self, max_length: int = 300) -> str:
        """生成可读摘要"""
```

### DivergencePoint 扩展

```python
@dataclass
class DivergencePoint:
    # ... 原有字段 (class_name, diverge_line, only_in_a, only_in_b) ...
    
    snapshot_a: Optional[VariableSnapshot] = None  # trace_a 的变量快照
    snapshot_b: Optional[VariableSnapshot] = None  # trace_b 的变量快照
```

## Java 端序列化规则

`SnapshotSerializer` 将 Java 对象转为 JSON，规则：

- **基本类型/String**: 直接值
- **集合/数组**: 展开前10个元素，超过则截断
- **Map**: 展开前10个键值对
- **POJO**: 取所有实例字段（非 static, 非 transient）
- **嵌套对象**: 仅序列化1层，嵌套对象用 `toString()`
- **字符串**: 最多200字符，超过截断
- **整体快照**: 最多2000字符

## 性能考虑

- **第一阶段**: 与现有 trace 性能相同（无额外开销）
- **第二阶段**: 仅对指定方法采集快照
  - 每个方法增加约 1-5ms 开销（序列化时间）
  - 对象序列化复杂度: O(字段数)，截断保护防止过大对象
  - 推荐只快照偏差相关的少数方法（<20个）

## 限制与注意事项

1. **Java 端必须使用 trace-agent**（支持变量快照）
2. **需要重新构建 trace-agent**：`cd trace-agent && mvn clean package`
3. **参数名需要编译时保留**：编译时加 `-parameters` 标志（否则显示为 arg0, arg1）
4. **不支持局部变量**：只能捕获方法参数、返回值、this 字段
5. **大对象截断**：避免序列化过大，单个快照最多 2KB
6. **循环引用安全**：序列化深度限制为1层，避免无限递归

## 重新构建 trace-agent

```bash
cd trace-agent
mvn clean package
# 生成 target/trace-agent-1.0.0.jar

# 使用新 agent 启动微服务
java -javaagent:trace-agent/target/trace-agent-1.0.0.jar=packages=com.example \
     -jar your-service.jar
```

## 故障排查

**问题**: 快照字段为空（args_snapshot / return_snapshot / this_snapshot 都是 null）

**可能原因**：
1. 请求头未正确设置：确保同时有 `X-Return-Trace: true` 和 `X-Snapshot-Methods: <methods>`
2. 方法名匹配失败：检查 `X-Snapshot-Methods` 格式，尝试用简单类名或 `*`
3. trace-agent 未更新：确保使用新构建的 trace-agent-1.0.0.jar
4. 目标方法未被插桩：检查 agent 启动日志，确认 packages 参数包含目标类

**调试步骤**：
```python
# 1. 测试全量快照
headers = {"X-Return-Trace": "true", "X-Snapshot-Methods": "*"}
trace = execute_request(params, headers=headers)

# 2. 检查是否有任何节点带快照
has_snapshot = any(
    n.args_snapshot or n.return_snapshot or n.this_snapshot
    for n in trace.nodes
)
print(f"是否有快照: {has_snapshot}")

# 3. 列出所有有快照的节点
for node in trace.nodes:
    if node.args_snapshot or node.return_snapshot:
        print(f"{node.class_namespace}.{node.function}: 有快照")
```

## 后续增强方向

- [ ] 支持采集方法内局部变量（需要 JVMTI 或更深层次的字节码插桩）
- [ ] 支持变量快照的增量 diff（对比 trace_a 和 trace_b 中相同方法的变量差异）
- [ ] 自动推荐最小快照方法集（基于调用图分析）
- [ ] 可视化变量快照（Web UI 展示对象字段树）
