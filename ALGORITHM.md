# 核心算法设计

## 1. 预期路径生成

### 问题定义

给定：
- 调用图 G = (V, E)，其中 V 为方法节点，E 为调用边（部分边标记为 taint 边）
- API 入口集合 A ⊆ V
- Sink（日志打印点）集合 S ⊆ V

求：对每个可达的 (a, s) ∈ A × S，生成一条从 a 到 s 的最优方法调用序列。

### 算法：BFS + 最优路径选择

```
算法 GenerateExpectedPaths(G, A, S)

输入: 调用图 G, API 入口集合 A, Sink 集合 S
输出: 预期路径集合 P

常量:
  MAX_LENGTH = 20       // 路径最大长度
  MAX_PER_PAIR = 1      // 每个 (API, Sink) 对最多保留的路径数

P ← ∅

for each api ∈ A:
    // BFS 从 api 出发遍历调用图
    queue ← [(api, [api])]     // (当前节点, 路径)
    visited ← {api}
    reachable_sinks ← {}       // sink → 所有到达该 sink 的路径

    while queue 非空:
        (node, path) ← queue.dequeue()

        if len(path) > MAX_LENGTH:
            continue

        if node ∈ S:
            reachable_sinks[node].append(path)

        for each (node, next) ∈ E:
            if next ∉ visited:
                visited.add(next)
                queue.enqueue((next, path + [next]))

    // 对每个可达的 sink，选择最优路径
    for each (sink, paths) ∈ reachable_sinks:
        best ← SelectBestPath(paths)
        P.add(ExpectedPath(api, sink, best))

return P
```

### 路径选择策略

```
算法 SelectBestPath(paths)

输入: 同一 (API, Sink) 对的候选路径列表
输出: 最优路径

// 优先级: taint 路径 > 调用图路径, 短路径 > 长路径
taint_paths ← [p ∈ paths | p 中包含 taint 边]

if taint_paths 非空:
    return argmin(taint_paths, key=len)
else:
    return argmin(paths, key=len)
```

### 路径置信度

```
confidence(path) =
    1.0   如果 path 全部由 taint 边组成
    0.7   如果 path 包含至少一条 taint 边
    0.5   如果 path 仅由普通调用图边组成
```

### 输出格式

每条预期路径为一个有序方法序列：

```
ExpectedPath = {
    api_entry: (http_method, http_path, class, method),
    log_sink:  (class, method, line, log_level, log_api),
    nodes:     [PathNode₀, PathNode₁, ..., PathNodeₙ],
    source:    "taint" | "call_graph",
    confidence: float ∈ [0, 1]
}

PathNode = {
    class_name: string,    // 完全限定类名
    method:     string,    // 方法名
    depth:      int        // 路径中的位置 (0 = API 入口)
}
```

---

## 2. 偏差计算（Reachability Progress Analysis）

### 问题定义

给定：
- 预期路径 EP = [n₀, n₁, ..., nₖ]（静态方法序列）
- 实际 Trace T = {TraceNode₁, TraceNode₂, ...}（运行时采集的方法调用记录）

求：执行流在预期路径上第一次"断裂"的位置（Reachability Progress）。

### 算法：双策略匹配取最优

```
算法 ComputeDivergence(EP, T)

输入: 预期路径 EP, 实际运行 Trace T
输出: PathDivergence (reached_depth, first_missed_node, reach_rate)

if T 为空:
    return Divergence(reached_depth=0, reason="not_started")

// 策略一: 树对齐
tree_depth ← TreeAlign(T, EP)

// 策略二: 平铺搜索
flat_depth ← FlatAlign(T, EP)

// 取最优结果
reached_depth ← max(tree_depth, flat_depth)

if reached_depth == len(EP):
    return Divergence(reached_depth, reason="full_reach", reach_rate=1.0)
else:
    return Divergence(
        reached_depth,
        first_missed = EP[reached_depth],
        reach_rate   = reached_depth / len(EP),
        reason       = "partial_reach"
    )
```

### 策略一：树对齐

利用 trace 中的 parent-child 关系（span_id / parent_span_id）构建调用树，沿预期路径逐层向下匹配。

```
算法 TreeAlign(T, EP)

输入: Trace T (含调用树), 预期路径 EP = [n₀, n₁, ..., nₖ]
输出: 到达深度 (int)

// 构建调用树
roots ← BuildTree(T)    // parent_span_id 为空的节点为根
candidates ← roots      // 当前层的候选节点
reached ← 0

for i = 0 to k:
    matched ← null

    for each node ∈ candidates:
        if Match(node, EP[i]):
            matched ← node
            break

    if matched ≠ null:
        reached ← i + 1
        candidates ← matched.children    // 下一轮在子节点中搜索
    else:
        break    // 断裂: 当前层找不到匹配

return reached
```

**匹配函数**：

```
算法 Match(trace_node, path_node)

return Normalize(trace_node.class_namespace) == Normalize(path_node.class_name)
       AND trace_node.function == path_node.method

// Normalize: 将 '/' 替换为 '.', 统一大小写
```

**特点**：
- 精确：不会误匹配被其他调用链触发的同名方法
- 依赖：需要正确的 parent_span_id

### 策略二：平铺搜索

忽略调用树结构，在 Trace 的所有节点中逐个匹配预期路径节点。

```
算法 FlatAlign(T, EP)

输入: Trace T (所有节点的平铺列表), 预期路径 EP = [n₀, n₁, ..., nₖ]
输出: 到达深度 (int)

all_nodes ← Flatten(T)    // 所有 TraceNode，忽略层级
reached ← 0

for i = 0 to k:
    found ← false

    for each node ∈ all_nodes:
        if Match(node, EP[i]):
            found ← true
            break

    if found:
        reached ← i + 1
    else:
        break    // 断裂: 全局都找不到该方法

return reached
```

**特点**：
- 鲁棒：不依赖 parent_span_id，即使调用树不完整也能工作
- 宽松：可能产生假阳性（方法被执行了，但不是通过预期调用链到达的）

### 为什么取 max

```
final_depth = max(tree_depth, flat_depth)
```

- 树对齐可能因树结构不完整（中间节点缺失）而低估实际到达深度
- 平铺搜索可能高估（但不会漏报已到达的方法）
- 取 max 保证不遗漏已到达的路径节点

### 输出格式

```
PathDivergence = {
    reached_depth:     int,           // 到达了预期路径的第几个节点
    first_missed_node: PathNode,      // 第一个未命中的节点 (null if full_reach)
    reach_rate:        float ∈ [0,1], // reached_depth / total_nodes
    divergence_reason: "full_reach" | "partial_reach" | "not_started"
}
```

### 示例

```
预期路径: [Controller.save, Service.validate, Service.persist, Repository.insert]
实际 Trace 节点: {Controller.save, Service.validate}

TreeAlign:
  roots 中找到 Controller.save → matched, candidates = children
  children 中找到 Service.validate → matched, candidates = children  
  children 中找不到 Service.persist → break
  → tree_depth = 2

FlatAlign:
  全局找 Controller.save → found
  全局找 Service.validate → found
  全局找 Service.persist → not found → break
  → flat_depth = 2

结果:
  reached_depth = 2
  first_missed_node = Service.persist
  reach_rate = 2/4 = 50%
  reason = "partial_reach"

反馈给 LLM: "请求通过了 validate 校验，但未到达 persist 方法。
             可能原因: validate 返回了错误 / 中间有条件分支未满足。
             建议: 构造能通过 validate 逻辑的参数。"
```

---

## 3. 批量偏差计算

当一个 API 对应多条预期路径（通向不同 Sink）时，需要选出最佳匹配：

```
算法 BestMatch(trace, expected_paths)

输入: 实际 Trace, 该 API 的所有预期路径
输出: 最佳匹配的 (ExpectedPath, PathDivergence)

results ← []
for each path ∈ expected_paths:
    div ← ComputeDivergence(path, trace)
    results.append((path, div))

// 按 reach_rate 降序排序, 选最优
return argmax(results, key=div.reach_rate)
```

这样可以在多条预期路径中，自动选出与当前 trace 最匹配的那条，作为 Fuzz 反馈的依据。
