# aiops-py

基于 LLM Agent 的 Java 微服务安全测试框架：通过灰盒偏差反馈 fuzz（源码 + 数据库状态 + 日志）自主完成日志注入 sink 的前置条件构造并触发。

**累计 16 个真实业务 sink 验证通过**（ruoyi-plus 10 + pig 5 + yudao pay/mall 幂等域 5 + small-swarm 3，部分为概念家族合并计数），另有「静态不可达家族」（5 处）作为阴性对照。完整数据见 [docs/RESULTS-SUMMARY.md](docs/RESULTS-SUMMARY.md)。

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
├── docs/RESULTS-SUMMARY.md  # ⭐ 实验结果汇总 (九章, 含 harness 修复公平性说明)
├── logs/agent_runs/       # ⭐ 每次 Agent 运行的完整结果 JSON (29 个 reached=True)
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
# LLM_BASE_URL="http://10.1.0.180:8000/v1"
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

### 已完成（截至 2026-10-13, HEAD=8116d1a3）

**实验成果（29 个 reached=True 结果文件）**

| 项目 | 域 | PASS 数 | 备注 |
|------|-----|--------|------|
| ruoyi-plus | 菜单/工作流 10 概念 | 10 | 含黑盒对照 |
| pig | 文件/短信/密码/token 5 | 5 | 含 AES 协议逆向 |
| yudao pay | 幂等域 3 | 3 | demo-order L126 / wallet-recharge L131 / demo-withdraw L143 |
| yudao mall | 幂等域 2 | 2 | brokerage-withdraw L248 / trade-order L292 |
| small-swarm | CRUD 域 3 | 3 | brand create/update/delete (mall-demo, 9215) |

**关键发现（论文素材）**

- **幂等/重复回调日志模式 5/5 全命中**（pay 3 + mall 2）——db_state 池中最高价值模式
- **静态不可达家族**：「找不到对应的支付客户端」L75/L97/L119/L227/L246 五处，`validPayChannel` 先抛异常使 `if(payClient==null)` 成为死代码——静态可达性预过滤价值的实证
- v2→v4 三连跑构成 harness 可信度验证链（误报修复→错服务→阴性对照）

**基础设施**

- 26 个追踪镜像全部构建（`scripts/ops/build_and_push_all_traced.sh` 清单 18 + 后补 8，含 mall-demo）
- 已部署目标：ruoyi-plus(9201/9205)、yudao trade(9212)/pay(9213)/system(9214)、pig(9999 宿主机)、mall-demo(9215)、lamp-oauth(9216, 已启动)

### 未完成 TODO（按优先级）

1. **lamp-cloud 域（15 个候选，环境刚就绪）**
   - lamp-oauth-server 已启动（9216）；登录入口 `POST /anyTenant/login`
   - 登录链路已打通到"密码校验"环节：`Authorization` 头传**纯 base64** `lamp_web_pro:lamp_web_pro_secret`（lamp 自定义，无 Basic 前缀）；superAdmin 密码已用 SQL 重置为 123456
   - **当前卡点**：`BaseEmployeeMapper.listEmployeeByUserId` 报 `Invalid bound statement`——mapper XML 在 `lamp-base-biz-5.10.0.jar` 内 `mapper_base/` 目录存在，但 mybatis-plus `mapper-locations` 配置来自 nacos `mysql.yml`（**nacos 里没有该文件**）。修复方向：compose 补 `--mybatis-plus.mapper-locations=classpath*:mapper_*/**/*.xml`
   - 候选目标：`AbstractTokenGranter` L425（登录成功日志，最易）、`PasswordTokenGranter` L122（密码输错锁定分支，需 DB 造 `password_error_num`）、`ResourceBiz` L205/L256、`DefResourceServiceImpl` L196、`AbstractFileChunkStrategy` L146
2. **yudao system 域（9 个候选，阻塞中）**
   - 阻塞：`get-permission-info` 500 —— `WebFrameworkUtils.getLoginUserType` L115 NPE（静态 `properties==null`）
   - 已排除：类重复 / 构造赋值异常 / trace-agent 干扰（无 agent 对照仍 NPE）/ lazy-init / feign url 配置 / 排除 security+tenant（引发 mzt-biz-log 连锁失败已回滚）
   - 环境修复已完成：`SPRING_CLOUD_DISCOVERY_ENABLED=false`（common-real.env）是 nacos 自注册失败真根因——env_file 静默覆盖命令行参数，已用 compose `environment:` 覆盖为 true，自注册成功
   - 待查：`YudaoWebAutoConfiguration` 的 `webFrameworkUtils` bean 为何未创建（conditions report 显示 `#restTemplate matched`）；可对比 trade 的 starter-web 版本差异
3. **mall-search 域**：EsProduct recommend 候选需 Elasticsearch，未部署
4. **paascloud（37 个候选，最大单块）**：连 Dockerfile 都没有，需从零写
5. **gulimall（3 个）/ mall4cloud / passjava**：镜像已构建，compose 未建
6. **静态可达性预筛脚本**：识别 null-after-throw 死代码模式（pay 家族教训产品化），预期剔除 10-20% 候选
7. **论文收尾**：`docs/RESULTS-SUMMARY.md` 补 lamp/small-swarm 章节；图 2-8 用 `scripts/make_figures.py` 更新

### 环境速查

| 服务 | 容器 | 端口 | 说明 |
|------|------|------|------|
| MySQL | trace-real-mysql | 3306 | 库: ruoyi-vue-pro / pig / mall / lamp_none / ry-workflow |
| Redis | trace-real-redis | 6379 | 密码 password（yudao）/ db5（pig） |
| Nacos | trace-real-nacos | 8848 | nacos/nacos |
| yudao trade | trace-real-yudao-cloud-trade-server | 9212 | mall 域 sink |
| yudao pay | trace-real-yudao-cloud-pay-server | 9213 | pay 域 sink |
| yudao system | trace-real-yudao-cloud-...-system-server | 9214 | 阻塞中（TODO 2） |
| mall-demo | trace-real-mall-swarm-mall-demo | 9215 | small-swarm 域 |
| lamp-oauth | trace-real-lamp-cloud-lamp-oauth-server | 9216 | TODO 1（JVM 加 -Xmx1200m 防 OOM crash；jacoco agent 在 jdk17 会 premain 失败 SIGSEGV，已移除） |
| pig-boot | 宿主机进程 | 9999 | 日志 /tmp/pigboot.log |

### 已知坑速查（详见 docs/RESULTS-SUMMARY.md 第九章）

- yudao 主 SQL **不含** pay/mall 建表；建表技巧：MyBatis 报错信息会给完整 SELECT 列清单，照抄补列即可
- `common-real.env` 的 `SPRING_CLOUD_DISCOVERY_ENABLED=false` 会覆盖所有命令行参数——单项目需 discovery 时必须在 compose `environment:` 段覆盖
- mapper XML 缺失报 `Invalid bound statement` → 先查 nacos 对应 dataId 是否存在（lamp 的 mysql.yml 缺失）
- LLM 端点偶发断连，重跑即可；整轮 API 异常的运行=基础设施未执行，不计结果
- 目标服务日志看不到预期 sink 行 → 先查日志级别（mall-demo 的 controller 是 LOGGER.debug，需 `--logging.level.com.macro.mall=debug`）

## 关键文档

- [docs/RESULTS-SUMMARY.md](docs/RESULTS-SUMMARY.md) — 实验结果汇总表（九章）
- [timeline.md](timeline.md) — 两周排期与已验证 sink 台账
- [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md) — 目录结构详解
- [DESIGN.md](DESIGN.md) / [ALGORITHM.md](ALGORITHM.md) — 设计与算法
