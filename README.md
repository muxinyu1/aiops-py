# aiops-py

基于 LLM agent 的 Java 微服务安全测试框架：通过灰盒偏差反馈 fuzz（源码 + 数据库状态 + 日志）自主完成日志注入 sink 的前置条件构造并触发。

核心成果：**13 个真实业务 sink 全部验证可触发**（RuoYi-Cloud-Plus 10 + pig 3），覆盖单跳业务、流程引擎状态机、跨服务 dubbo、多租户隔离、客户端加密协议、服务端验证码六类难点。详见 [docs/RESULTS-SUMMARY.md](docs/RESULTS-SUMMARY.md)。

## 项目结构

```
├── sink_agent.py          # ⭐ 核心: LLM agent (B 方案: API-only, 无 DB 写权限)
├── main.py / pipeline.py  # 静态分析管线入口
├── data/                  # 实验数据 (sink 分类 / callgraph / api→表映射)
├── sinks/                 # 各项目 sink 清单 JSON
├── docs/                  # 汇报稿与实验汇总 (report-db-state-agent.md / RESULTS-SUMMARY.md)
├── logs/
│   ├── agent_runs/        # ⭐ 每次 agent 运行的完整结果 JSON (含工具调用序列)
│   └── archive/           # 历史项目产物与过期日志
├── scripts/
│   ├── demos/             # 各项目 demo fuzz 脚本
│   └── pipelines/         # 各项目静态分析管线入口
├── tools/                 # agent jar (jacoco / opentelemetry)
├── trace-agent/           # 自研执行路径追踪 agent (Java)
├── examples/              # 目标 Java 微服务源码 (20+ 开源项目)
├── examples-yml/          # 各项目 docker compose 部署清单 (compose.real.yaml)
└── tests/                 # 单元/端到端测试
```

## 快速开始

```bash
# 环境准备 (RuoYi-Cloud-Plus 示例)
cd examples-yml/RuoYi-Cloud-Plus
docker compose -f compose.real.yaml up -d

# 列出 sink 任务
uv run python sink_agent.py --list

# 运行单个 sink 任务 (标准灰盒模式)
uv run python sink_agent.py --sink ruoyi_plus_menu_same_level --output logs/agent_runs/run.json

# 黑盒对照 (不给 API/环境提示)
uv run python sink_agent.py --sink ruoyi_plus_menu_same_level --hint-level none
```

## 关键文档

- [docs/report-db-state-agent.md](docs/report-db-state-agent.md) — 汇报稿（方法 + 全部实验）
- [docs/RESULTS-SUMMARY.md](docs/RESULTS-SUMMARY.md) — 实验结果汇总表
- [DESIGN.md](DESIGN.md) / [ALGORITHM.md](ALGORITHM.md) — 设计与算法
