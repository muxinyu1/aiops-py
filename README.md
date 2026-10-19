# aiops-py

基于 LLM Agent 的 Java 微服务安全测试框架：通过灰盒偏差反馈 fuzz（源码 + 数据库状态 + 日志）自主完成日志注入 sink 的前置条件构造并触发。

**累计 100+ 个真实业务 sink 验证通过**（yudao 55 + ruoyi-plus 10 + lamp-cloud 9 + pig 6 + small-swarm 5 + apollo 5 + 散量微服务 10，累计 103 个验证目标/107 个 reached 结果文件），另有多处「静态不可达死代码」作为阴性对照。完整数据见 [docs/RESULTS-SUMMARY.md](docs/RESULTS-SUMMARY.md) 与 [timeline.md](timeline.md)。

> ⚠️ **交接文档**：本 README 面向接手的新 Agent/开发者。当前进度、环境状态与 TODO 见文末「交接：进度与 TODO」章节。

## 项目结构

```
├── sink_agent.py          # ⭐ 核心: LLM Agent (B 方案: API-only, 无 DB 写权限)
│                          #   SINK_TASKS 定义全部任务; log_match 字段做精确日志判定
├── timeline.md            # ⭐ 两周实验排期与已验证 sink 台账 (进度主线)
├── PROJECT_STRUCTURE.md   # 目录结构详解 (2026-10 重组后)
├── static_analysis.py     # 静态分析管线主入口
├── data/                  # 实验数据 (sink_llm_classify.json 2230 条分类 / api_table_map / callgraph)
├── sinks/                 # 各项目 sink 清单 JSON
├── docs/RESULTS-SUMMARY.md  # ⭐ 实验结果汇总 (二十八章, 含 harness 修复公平性说明)
├── logs/agent_runs/       # ⭐ 每次 Agent 运行的完整结果 JSON (107 个 reached=True)
├── scripts/
│   ├── analysis/          # 静态分析辅助 (api_discovery / codeql / joern 适配器等)
│   ├── ops/               # 镜像构建脚本 (build_and_push_all_traced.sh)
│   ├── pipelines/         # 各项目端到端管线
│   └── demos/             # 人工 setup 的 demo fuzz 脚本
├── examples/              # 目标 Java 微服务源码 (20+ 开源项目)
├── examples-yml/          # 各项目 docker compose 部署清单 (compose.real.yaml)
├── trace-agent/           # 自研执行路径追踪 Java Agent (X-Return-Trace / X-Execution-Trace)
└── tests/                 # 单元测试 (81 通过) + e2e (需 8080 服务在线)
```

## 快速开始

```bash
# 1. 配置 LLM (本地 vLLM)
cat .env
# LLM_BASE_URL="http://10.1.1.20:8000/v1"
# LLM_MODEL="model"
# ⚠ vLLM 启动必须带: --enable-auto-tool-choice --tool-call-parser hermes

# 2. 列出 sink 任务
python3 sink_agent.py --list

# 3. 运行单个 sink (标准灰盒模式)
python3 sink_agent.py --sink small_swarm_brand_create \
  --max-turns 10 --max-tools 40 \
  --output logs/agent_runs/run.json

# 4. 黑盒对照 (不给 API/环境提示)
python3 sink_agent.py --sink small_swarm_brand_create --hint-level none
```

### 运行要点

- 结果判定**不信 Agent 自述**：harness 用任务里的 `log_match`（目标日志前缀）对比运行基线增量
- 日志参数放不进 marker 的任务（数值/对象参数）必须用 `log_match`；marker 放 query/body 仅用于日志取证
- 本地 vLLM 单次调用 30-60s，单 sink 实验约 5-30 分钟；偶发超时重跑即可

---

## 交接：进度与 TODO

### 已完成（截至 2026-10-19, HEAD=cb3cb26d）

**实验成果（100+ 个业务 sink 验证通过，107 个 reached=True 结果文件）**

| 项目 | 域 / 核心机制 | PASS 数 | 备注 |
|------|--------------|--------|------|
| yudao pay | 订单/退款/充值/提现/转账/钱包 状态机与幂等 | 34 | 含状态同步、金额与商户号校验、跨表异常分支全部收敛 |
| yudao mall | 订单支付/分销转账/售后退款 校验 | 16 | 含跨服务 Feign 数据不一致多维校验 |
| yudao system | 菜单拓扑孤儿/模板状态/数据权限/操作日志 | 5 | 修复 trace-agent 与数据权限环境打通 |
| ruoyi-plus | 菜单/工作流 10 概念 | 10 | 含 workflow 状态机、多租户绕过与黑盒对照 |
| lamp-cloud | OAuth 登录/锁定/Token刷新/路由树构建/权限 | 9 | 解决 SpringUtils 上下文注入与 ip2region 依赖 |
| pig | 文件/短信/密码/token/API Key 锁定 | 6 | 含 AES 协议逆向与验证码解码 |
| small-swarm | 品牌 CRUD 成功与失败分支 (mall-demo 9215) | 5 | 全池可达候选清零 |
| apollo | 应用/命名空间删除/Token鉴权/密钥上限/配置覆写 | 5 | adminservice (8080) 可达候选 100% 收敛 |
| mogu-blog | 管理员登录密码错误 / @AuthorityVerify 鉴权 | 2 | 独立镜像 (8080) |
| novel-cloud | JWT 解析校验失败异常捕获 | 1 | 拦截器触发告警日志 (8080) |
| RuoYi-Cloud | 认证模块非法 Token 拦截 | 1 | ruoyi-auth (8080) |
| zlt-uaa | 短信验证码未失效校验 | 1 | Redis 缓存状态联动 (8080) |
| youlai-mall | 订单提交参数校验与记录 | 1 | mall-oms (8803) |
| mall4cloud | Base64 刷新令牌解密异常处理 | 1 | mall4cloud-auth (8080) |
| PiggyMetrics | OAuth2 保护端点用户信息拉取异常 | 1 | account-service (8080) |
| Cloud-Platform | 身份认证请求用户名审计 | 1 | ace-admin (8080) |
| java-microservice | VIP大额APP端多层风控条件判断 | 1 | 嵌套条件分支命中 (8080) |
| **合计** | **17 个业务微服务系统** | **100+** | **累计 103 验证概念目标，107 个 reached 文件** |

**关键发现（论文素材）**

- **幂等/重复回调日志模式全命中**（pay 3 + mall 2）——db_state 池中最高价值模式。
- **静态不可达死代码实证**：累计证明 19 处死代码/环境受限分支（如 yudao pay「找不到支付客户端」8 处防御性死代码、mall payTransfer NPE、pig `isExpired` 硬编码真、SpringBlade 空对象序列化等），展现静态可达性分析在消除盲目 fuzz 上的关键价值。
- **多租户与数据隔离绕过**：Agent 在 ruoyi-workflow 中通过修改租户字段创造逻辑孤儿突破业务删除拦截；在 yudao system 中自主阅读源码分析数据范围枚举，构造越权数据范围触发审计日志。
- **环境自适应推理**：黑盒对照验证了 Agent 拥有自主探明微服务拓扑的能力，环境提示提升了效率（2.4× 成本优化）而非能力开关。

**基础设施**

- 26 个追踪镜像全部构建（`scripts/ops/build_and_push_all_traced.sh` 清单 18 + 后补 8，含 mall-demo、lamp-oauth 等）。
- 已部署并打通目标：ruoyi-plus(9201/9205)、yudao trade(9212)/pay(9213)/system(9214)、pig(9999 宿主机)、mall-demo(9215)、lamp-oauth(9216)、apollo-adminservice(8080)。

### 未完成 TODO（按优先级）

1. **paascloud（37 个候选，当前核心攻坚项）**
   - MySQL 8 驱动兼容性已修复（pom.xml `mysql.version` 更新为 8.0.28，commit cb3cb26d）。
   - 下一步：编写独立微服务 Dockerfile 与 compose 配置（优先 `paascloud-provider-uac`），导入数据库初始表结构并启动 fuzz。
2. **论文图表生成与收尾**
   - 使用 `scripts/make_figures.py` 更新图 2-8，基于 `logs/agent_runs/*.json` 完整数据重绘成本与分类分布图。
3. **外部受限候选标记**
   - mall-search 候选需 Elasticsearch（未部署）。
   - yudao Coupon L204 需 promotion-server Feign 依赖。

### 环境速查

| 服务 | 容器 | 端口 | 说明 |
|------|------|------|------|
| MySQL | trace-real-mysql | 3306 | 库: ruoyi-vue-pro / pig / mall / lamp_none / ry-workflow / ApolloConfigDB |
| Redis | trace-real-redis | 6379 | 密码 password（yudao）/ db5（pig） |
| Nacos | trace-real-nacos | 8848 | nacos/nacos |
| yudao trade | trace-real-yudao-cloud-trade-server | 9212 | mall 域 sink（16 PASS，全收敛） |
| yudao pay | trace-real-yudao-cloud-pay-server | 9213 | pay 域 sink（34 PASS，全收敛） |
| yudao system | trace-real-yudao-cloud-...-system-server | 9214 | system 域 sink（5 PASS，全收敛） |
| mall-demo | trace-real-mall-swarm-mall-demo | 9215 | small-swarm 域（5 PASS，全收敛） |
| lamp-oauth | trace-real-lamp-cloud-lamp-oauth-server | 9216 | lamp-cloud 域（9 PASS，全收敛） |
| apollo | trace-real-apollo-apollo-adminservice | 8080 | apollo 域（5 PASS，全收敛） |
| pig-boot | 宿主机进程 | 9999 | 日志 /tmp/pigboot.log（6 PASS，全收敛） |

### 已知坑速查（详见 docs/RESULTS-SUMMARY.md）

- yudao 主 SQL **不含** pay/mall 建表；建表技巧：MyBatis 报错信息会给完整 SELECT 列清单，照抄补列即可。
- `common-real.env` 的 `SPRING_CLOUD_DISCOVERY_ENABLED=false` 会覆盖所有命令行参数——单项目需 discovery 时必须在 compose `environment:` 段覆盖。
- mapper XML 缺失报 `Invalid bound statement` → 补 `--mybatis-plus.mapper-locations`。
- LLM 端点偶发断连，重跑即可；整轮 API 异常的运行=基础设施未执行，不计结果。
- 目标服务日志看不到预期 sink 行 → 先查日志级别（mall-demo 的 controller 是 LOGGER.debug，需 `--logging.level.com.macro.mall=debug`；lamp 需 `--logging.level.top.tangyh=debug`）。

## 关键文档

- [docs/RESULTS-SUMMARY.md](docs/RESULTS-SUMMARY.md) — 实验结果汇总表（二十八章）
- [timeline.md](timeline.md) — 两周排期与已验证 sink 台账
- [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md) — 目录结构详解
- [DESIGN.md](DESIGN.md) / [ALGORITHM.md](ALGORITHM.md) — 设计与算法

