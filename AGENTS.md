# AGENTS.md

> 本文件是给 AI Agent 的操作手册。目标：不依赖人类解释即可在本仓库安全、高效地工作。
> 背景知识见 README.md（人类向）；本文只写 Agent 需要的**操作事实**。

## 0. 你在这个项目里做什么

这是一个实验型安全研究仓库：用 LLM Agent（`sink_agent.py`）通过 HTTP API 触达 Java 微服务里预标记的日志 sink（log.info/warn/error），验证"Agent 能否自主构造 db_state 前置条件并命中目标日志"。

你的工作通常是：**跑实验 → 核验结果 → 修复环境 → 提交成果 → 更新台账**。

## 1. 铁律（违反会导致实验作废）

1. **结果判定只信 harness**：`logs/agent_runs/*.json` 的 `reached` 字段由 `_count_sink_hits()` 对比基线判定。Agent 自述的"成功"一律不算数。
2. **API-only**：Agent 实验中禁止直接写数据库（`execute_sql` 已禁用）。给 Agent 造前置数据是你（harness 侧）的工作，不算违规。
3. **一轮全 API 异常的运行 = 未执行**：LLM 端点断连时会出现 0 工具调用的 JSON，这种文件不计入实验结果，重跑并换文件名（`_v2`/`_v3`…，不要覆盖历史）。
4. **不要覆盖历史结果文件**，哪怕是失败的。失败链（v1→v2→v4）是论文素材。
5. **改 sink 任务判定前先读 `_count_sink_hits()` 与 `log_match` 语义**：有 `log_match` 的任务按目标日志前缀逐行匹配；没有的按 `SINK_REACHED` marker 计数。两种判定不能混。

## 2. 关键命令

```bash
# LLM 配置在 .env（vLLM: http://10.1.0.180:8000/v1, model=model, 无需 key）
# ⚠ vLLM 启动必须带 --enable-auto-tool-choice --tool-call-parser hermes，否则工具调用 400

source .env && set -a   # 或 set -a && source .env && set +a

# 列任务 / 跑任务
python3 sink_agent.py --list
python3 sink_agent.py --sink <任务名> --max-turns 10 --max-tools 40 \
  --output logs/agent_runs/<任务名>_v1.json
# 判定任务难度: 简单 CRUD 用 10/40; 复杂 db_state 用 15/60

# 核验一次运行（必做）
python3 - <<'EOF'
import json
d = json.load(open('logs/agent_runs/<文件>.json'))
print({k: d.get(k) for k in ('reached','turns','tool_calls','elapsed')})
for x in d.get('tool_log', []):
    if x['tool'] == 'send_http':
        print(x['n'], x['args'].get('method'), x['args'].get('url','')[:70],
              (x['result'].split('\n')[0] if x.get('result') else '?'))
EOF

# 看目标服务真实日志（判定命中证据）
docker logs --since 30m <容器名> 2>&1 | grep -E '<目标日志关键词>'

# 单测（e2e 那 3 个失败是环境依赖，非回归）
pytest tests/ -q

# 提交（logs/agent_runs/ 已 gitignore 放行，必须随实验一起提交）
git add -A && git commit
```

## 3. 环境速查

LLM：vLLM `http://10.1.0.180:8000/v1`（`.env`）。单次调用 30-60s；偶发断连/Read timeout → 重跑即可，不是代码问题。

| 服务 | 容器 | 端口 | 库/凭证 |
|------|------|------|---------|
| MySQL | trace-real-mysql | 3306 | root/123456；库：ruoyi-vue-pro、pig、mall、lamp_none、ry-workflow |
| Redis | trace-real-redis | 6379 | password=password（yudao）/ pig 用 db5 |
| Nacos | trace-real-nacos | 8848 | nacos/nacos |
| yudao trade | trace-real-yudao-cloud-trade-server | 9212 | mall 域 sink |
| yudao pay | trace-real-yudao-cloud-pay-server | 9213 | pay 域 sink |
| yudao system | trace-real-yudao-cloud-…-system-server | 9214 | **阻塞**（见 §5.2） |
| mall-demo | trace-real-mall-swarm-mall-demo | 9215 | small-swarm 域 |
| lamp-oauth | trace-real-lamp-cloud-lamp-oauth-server | 9216 | **卡点**（见 §5.1） |
| pig-boot | 宿主机进程 | 9999 | 日志读文件 /tmp/pigboot.log |

宿主机 13G 内存基本占满——启动新服务前先停不用的容器；给新 JVM 加 `-Xms512m -Xmx1200m -XX:MaxMetaspaceSize=256m`。

## 4. 任务清单与判定方式

`python3 sink_agent.py --list` 是唯一权威来源。已 PASS 的 16 个 sink 台账在 `timeline.md` 和 `docs/RESULTS-SUMMARY.md` 第九章。

给新任务加 `log_match` 的规则：
- 日志参数是**数值/对象**（marker 放不进去）→ 必须用 `log_match`（目标日志前缀，如 `"[updateOrderPaid][order("`）
- 日志参数是**可控字符串** → 可用默认 marker 判定
- 前缀要足够特异，别让通用请求日志（如 `ApiAccessLogInterceptor` 的 preHandle）匹配上——v2 误报教训

## 5. 当前阻塞点（接手后先看这里）

### 5.1 lamp-cloud（15 候选，环境就绪 90%）

- 登录入口 `POST http://localhost:9216/anyTenant/login`
- `Authorization` 头要传**纯 base64** `lamp_web_pro:lamp_web_pro_secret`（lamp 自定义，**没有** "Basic " 前缀；带前缀会报 `Illegal base64 character 20`）
- superAdmin 密码已 SQL 重置为 123456（sha256(pwd+salt)）
- **当前卡点**：`BaseEmployeeMapper.listEmployeeByUserId` → `Invalid bound statement`。XML 在 `lamp-base-biz-5.10.0.jar` 的 `mapper_base/` 里存在，但 mybatis-plus `mapper-locations` 来自 nacos `mysql.yml`——**nacos 里没有这个文件**（已验证，nacos 只有 mogu 的 6 个配置）
- **修复方向**：compose.real.yaml 给 lamp 加 `--mybatis-plus.mapper-locations=classpath*:mapper_*/**/*.xml` 后重启
- 通了之后候选按易到难：`AbstractTokenGranter` L425（登录成功日志）→ `PasswordTokenGranter` L122（密码输错锁定，需 UPDATE def_user 造 `password_error_num`）→ `ResourceBiz` L205/L256 → `DefResourceServiceImpl` L196 → `AbstractFileChunkStrategy` L146
- 首个任务用 `log_match: "登录成功"` 之类前缀

### 5.2 yudao system（9 候选，阻塞）

- 现象：`GET :9214/admin-api/system/auth/get-permission-info` → 500，`WebFrameworkUtils.getLoginUserType` L115 NPE（静态 `properties==null`）
- **已排除**（不要重走）：类重复定义 / 构造函数赋值（javap 验证过 putstatic）/ trace-agent 干扰（无 agent 对照仍 NPE）/ lazy-init / feign url 配置 / 排除 security+tenant（引发 mzt-biz-log `functionService` 连锁失败，已回滚）
- **已修复**：`SPRING_CLOUD_DISCOVERY_ENABLED=false`（common-real.env）静默覆盖命令行参数是 nacos 自注册失败真根因，已用 compose `environment:` 覆盖为 true，自注册已成功（nacos dev 命名空间可见实例）
- **下一步**：查 `YudaoWebAutoConfiguration` 的 `webFrameworkUtils` bean 为何未创建（`#restTemplate` bean matched 但静态字段仍 null——这矛盾指向 bean 创建顺序或另一个 classloader）；或对比 trade 的 starter-web 版本（trade 是 2026.05 非 jdk8 后缀，system 是 `2026.05-jdk8-SNAPSHOT`）
- 登录本身已通（POST /admin-api/system/auth/login → 200），system 域 9 个候选多数卡在 perm-info

### 5.3 其他

- paascloud（37 候选）：无 Dockerfile，从零写（参考 `examples/paascloud-master/` 结构与 mall-admin Dockerfile 模板）
- mall-search 的 recommend 候选需要 Elasticsearch，未部署
- yudao Coupon L204 需要 promotion-server（Feign 依赖），镜像未构建

## 6. 高频坑（血泪清单）

- **yudao 主 SQL 不含 pay/mall 建表**。建表技巧：直接调接口让 MyBatis 报错，错误信息会给完整 SELECT 列清单，照抄建表。`trade_order` 需要 6 轮补列（营销/积分/拼团/秒杀列都在订单主表）。
- **`common-real.env` 的 `SPRING_CLOUD_DISCOVERY_ENABLED=false` 会覆盖一切命令行参数**（env_file 优先于 command）。任何需要 discovery 的服务必须在 compose `environment:` 段覆盖。
- **日志级别**：mall-demo 的 DemoController 全是 `LOGGER.debug`，必须加 `--logging.level.com.macro.mall=debug`，否则命中了日志也不输出。
- **python 改 compose 后确认写入了**：曾因替换锚点没匹配静默失败，白跑一轮容器。
- **Dockerfile 首行 `# syntax=docker/dockerfile:1.4` 需要外网拉镜像**，网络不通就删掉这行；缺基础镜像用 `docker tag` 从本地已有镜像补（如 17-jdk-jammy → 17-jre-jammy）。
- **mapper 报 `Invalid bound statement`** = mapper XML 没加载，先查 nacos 有没有对应 dataId，再查 jar 里 XML 是否存在（`unzip -l`），最后补 `mapper-locations`。
- yml 顶层 key 不能重复（DuplicateKeyException 会让 Spring 启动失败）。
- Agent 在长上下文里不爱主动收尾：简单任务把 `--max-tools` 压到 35 以下，命中由 harness 判定不受影响。

## 7. 工作循环（每个 sink 一轮）

1. 从 `data/sink_llm_classify.json` 选候选（优先幂等/重复回调模式，命中率高）
2. 读源码确认触发条件真实可达（警惕 null-after-throw 死代码模式，见 `yudao_pay_channel_not_found` 的 `static_unreachable_note`）
3. 环境预置：建表/造数据/开日志级别（harness 侧，不走 Agent）
4. **自己 curl 预验证**目标日志确实输出（不预验证就上 Agent = 浪费 15 分钟）
5. 加任务到 `SINK_TASKS`（含 `log_match`）
6. 跑 Agent → 核验 JSON → 核验容器日志 → 换文件名保存
7. 更新 `timeline.md` 台账 + `docs/RESULTS-SUMMARY.md` + commit
