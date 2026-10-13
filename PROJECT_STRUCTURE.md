# aiops-py 项目结构

> LLM 驱动的 Java 微服务日志注入 sink 验证框架：静态分析定位 sink → Agent 灰盒 fuzz 触达 → harness 日志判定。

## 目录结构

```
aiops-py/
├── sink_agent.py          # ★ 核心: Sink-directed LLM 灰盒 Agent (SINK_TASKS + 工具集 + harness)
├── timeline.md            # 两周实验排期与已验证 sink 台账
│
├── 静态分析核心           # sink 提取/路径分析引擎
│   ├── static_analysis.py     # Joern+CodeQL 集成流水线主入口
│   ├── api_discovery → scripts/analysis/
│   ├── path_generator.py      # 调用图 → 预期路径
│   ├── expected_path.py / path_differ.py / differ.py / difference.py
│   └── trace.py / trace_span 相关
│
├── fuzz 引擎              # LLM fuzz 与执行
│   ├── llm.py / fuzzer.py / pipeline.py / executor.py / snapshot_executor.py
│   ├── parameter.py / source.py / sink.py / scene.py(骨架)
│   └── docker.py / jacoco_client.py / otlp_receiver.py
│
├── scripts/
│   ├── analysis/          # 静态分析辅助 (api_discovery/codeql/joern 适配器/
│   │                      #   analyze_reachability/classify_sinks/filter_sinks/api_table_map 等)
│   ├── ops/               # 镜像构建与推送 (build_*.sh, test_all_traced_projects.sh)
│   ├── pipelines/         # 各项目端到端 pipeline (run_yudao_pipeline.py 等)
│   ├── demos/             # 人工 setup 的 fuzz demo (demo_fuzz_*.py)
│   ├── make_figures.py    # 论文图表生成 (图2-图8)
│   ├── generate_fuzz_report.py / batch_trace_integration.py
│   └── figures_out/       # 图表输出 (gitignored)
│
├── tests/                 # 单元测试 + e2e (e2e 依赖 8080 目标服务在线)
├── data/                  # 静态分析产物 (sink 分类 2230 / api_table_map / callgraph)
├── sinks/                 # 各项目 sink 池 JSON
├── logs/agent_runs/       # ★ Agent 实验结果 JSON (已提交 git)
├── docs/                  # 中期报告 / 图表文档 / RESULTS-SUMMARY
├── reports/               # 阶段实验报告
│
├── examples/              # 20+ Java 微服务项目源码 (fuzz 目标)
├── examples-yml/          # 各项目 docker compose 部署清单 (真实环境)
│   └── yudao-cloud/       #   compose.trade.real.yaml (9212) / compose.pay.real.yaml (9213)
├── trace-agent/           # Java Agent (X-Return-Trace 执行路径追踪)
└── workspace/             # Joern CPG 二进制缓存 (gitignored)
```

## 常用入口

```bash
# Agent sink 实验
python3 sink_agent.py --list
python3 sink_agent.py --sink <name> --max-turns 15 --max-tools 60 --output logs/agent_runs/<name>.json

# 静态分析
python3 static_analysis.py                # 主流水线
python3 scripts/analysis/analyze_reachability.py   # 可达性两层分类

# 测试 (单元)
pytest tests/ -q                          # e2e 需 8080 服务在线

# 镜像构建
bash scripts/ops/build_traced_images.sh
```

## 实验环境

- 共享依赖: `trace-real-mysql` / `trace-real-redis` / `trace-real-nacos`
- 已部署目标: ruoyi-plus(9201/9205), yudao trade(9212)/pay(9213)/system(9214), pig(9999 宿主机), mall-demo(9215), lamp-oauth(9216)
- LLM: 本地 vLLM `http://10.1.1.20:8000/v1`，model=`model`（需 `--enable-auto-tool-choice --tool-call-parser hermes`）
- 完整环境速查与当前阻塞点: 见 `README.md` 交接章节与 `AGENTS.md` §3/§5
