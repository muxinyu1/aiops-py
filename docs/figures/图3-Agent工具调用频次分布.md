# 图 3:Agent 工具调用频次分布(行为特征图)

## 这张图要表达什么

Agent 的工作重心是"理解与诊断"而非"盲试请求":源码理解类工具(read_source+search_code)与数据库诊断(query_db)合计约占 70%,真正的 HTTP 请求仅约 20%。这与黑盒 fuzz 的"纯请求轰炸"形成本质区别。

## 数据(31 次运行全量统计,已核实)

| 工具 | 调用次数 | 占比 | 功能定位 |
|---|---|---|---|
| read_source | 493 | 28.0% | 读 sink/Service/Controller/BO 源码 |
| search_code | 463 | 26.3% | 正则搜 Mapper XML/常量/过滤器 |
| query_db | 405 | 23.0% | 只读查表结构与现有数据 |
| send_http | 361 | 20.5% | 前置 API + 触发请求 |
| read_logs | 132 | 7.5% | 执行路径偏差反馈 |
| redis_cmd | 87 | 4.9% | 缓存状态 (集中在 pig 验证码/锁定场景) |
| docker_ps | 25 | 1.4% | 容器定位 |
| get_api_catalog | 3 | 0.2% | API→表目录 |
| execute_sql | 14 | 0.8% | (仅早期框架调试运行,现行框架已移除,画图时建议剔除) |

**剔除 execute_sql 调试运行后的现行框架口径(建议画图采用):**
read_source 493 / search_code 463 / query_db 405 / send_http 361 / read_logs 132 / redis_cmd 87 / docker_ps 25 / get_api_catalog 3,总 1748。

## 建议画法

**方案 A(推荐):水平条形图,按功能分组着色**
- 理解类(read_source, search_code)→ 蓝
- 诊断类(query_db, redis_cmd)→ 绿
- 执行类(send_http)→ 橙
- 反馈类(read_logs)→ 红
- 辅助类(docker_ps, get_api_catalog)→ 灰
每条标注次数与百分比,顶部加一句注释:"理解+诊断 ≈ 77%,HTTP 执行仅 20.5%"。

**方案 B:每个 sink 一行的堆叠条形图**
x 轴为 13 个命中 sink(按成本排序),每行是工具分布堆叠。能同时展示"不同场景的工具使用倾向"(如 pig_sms 的 redis_cmd 突出、mybatis_duplicate 的 read_source 突出),信息量大但对画图工具要求高。

## 每任务明细(方案 B 用,已核实)

| sink(命中运行) | read_source | search_code | query_db | send_http | read_logs | redis_cmd | docker_ps |
|---|---|---|---|---|---|---|---|
| pig_file_not_exist (13) | 4 | 3 | 3 | 2 | 1 | 0 | 0 |
| menu_same_level (26) | 8 | 5 | 7 | 4 | 1 | 0 | 1 |
| menu_route_name (30) | 10 | 5 | 10 | 3 | 1 | 0 | 1 |
| wf_inst_var (33) | 6 | 8 | 11 | 5 | 2 | 0 | 1 |
| wf_def_inuse (32) | 8 | 7 | 12 | 4 | 1 | 0 | 0 |
| wf_def_sync (36) | 8 | 10 | 11 | 4 | 1 | 0 | 1 |
| menu_root (53) | 9 | 7 | 11 | 16 | 9 | 0 | 1 |
| pig_change_password (49) | 7 | 15 | 12 | 9 | 5 | 0 | 1 |
| mybatis_duplicate (62) | 18 | 16 | 14 | 11 | 0 | 0 | 0 |
| pig_sms_login (78) | 24 | 30 | 9 | 9 | 1 | 4 | 1 |
| pig_sms_v2 (80) | 17 | 21 | 4 | 5 | 3 | 29 | 1 |
| wf_listener_end_v3 (104) | 27 | 25 | 33 | 10 | 8 | 0 | 1 |
| wf_inst_orphan_v2 (86) | 15 | 17 | 22 | 18 | 13 | 0 | 1 |
