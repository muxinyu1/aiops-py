# 执行路径追踪改造全流程记录

## 项目目标

为 `examples/` 下的 18 个 Java Spring Boot 微服务示例项目集成"执行路径追踪"能力，构建 Docker 镜像并推送到阿里云 ACR，最终通过真实业务 API 验证全部 18 个项目的追踪链路。

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
