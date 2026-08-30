# ARCHITECTURE.md

> 本文档描述目标架构与关键设计决策。当前代码为“初始化骨架”，尚未实现全部功能；实现进度见 `HANDOFF.md`。

## 1. 模块划分

- `monitor.py`：探针端（Agent），由原单机程序演进而来。
  - 保留原全部功能：读取 `/proc/net/dev`、SQLite 增量落库、本地面板、网卡明细、重置日、历史周期、手动录入。
  - 新增（后续实现）：当配置了 `hub_url` 与 `agent_token` 时，周期性向 Hub 上报本周期累计流量；未配置时行为与原来完全一致。
- `hub.py`：主控端（Hub）。
  - 接收各 Agent 上报，落库并聚合每日流量。
  - 提供多节点 Dashboard、7 日统计、阈值配置、预测告警、邮件发送。
- `install.sh` / `uninstall.sh`：安装/更新/卸载脚本，后续扩展为支持 `agent` / `hub` 两种角色。
- `tests/`：标准库 unittest 测试，覆盖数据层与预测算法等纯逻辑。
- `docs/`：架构与设计计划文档。

## 2. 关键设计决策

1. **单文件 + 标准库优先**：延续原项目风格，不引入框架。Hub 与 Agent 各自单文件，便于 `install.sh` 直接部署。
2. **推模式**：Agent 主动向 Hub 推送（`POST /api/report`），Hub 不需要主动 SSH 登录各 VPS，适合 Agent 位于 NAT / 动态 IP 后的场景。
3. **上报“本周期累计流量”而非原始网卡计数器**：Agent 复用自身 `Store.snapshot()` 的本周期合计；Hub 通过相邻两次上报的差值计算增量，天然处理网卡计数器回退与 Agent 重启。
4. **每日聚合表**：Hub 使用 `daily_usage(node_id, day, rx_bytes, tx_bytes)` 预聚合，7 日统计与预测都基于该表；原始上报保留在 `samples` 用于排查/回填。
5. **日切分时区**：统一默认北京时间（UTC+8，`480` 分钟）。Agent 上报 `tz_offset_minutes`，未上报时 Hub 默认 480；Hub 按该时区切分“天”并计算距离重置日的剩余天数。
6. **阈值预测触发**：`预计总流量 = 本周期已用 + 近 7 日日均 × 剩余天数`；若预计值超过该节点阈值则告警。
7. **邮件防抖**：同一节点同一告警状态在 `alert_cooldown_hours`（默认 24 小时）内只发一次，避免刷屏；告警记录落 `alerts` 表。
8. **鉴权**：Agent 用 `token`（Hub 生成，Agent 配置存明文，Hub 存 SHA-256）；管理员沿用原 session cookie 机制。
9. **向后兼容**：Agent 的 `hub_url` 为空时完全等同原单机程序；原数据库 schema 不变。

## 3. 主要数据流

```text
VPS Agent (monitor.py)
  /proc/net/dev -> read_net_dev -> Store.ingest
      -> Store.snapshot(本周期累计, 接口明细, 速率)
      -> 若配置 hub_url: POST /api/report
                 |
                 v
Hub (hub.py)
  /api/report -> 鉴权 token -> 更新 nodes.last_seen
      -> 计算与上次上报的增量 -> 写入 daily_usage
      -> 阈值预测任务 -> 若预计超限: 记录 alerts + 发邮件
                 |
                 v
  Dashboard /api/status -> 节点列表、7 日统计、告警历史
```

## 4. 必须先明确的问题

- Hub 与 Agent 的通信协议字段（`token`、`cycle`、`interfaces`、`tz_offset_minutes`）。
- “一天”的切分规则与“剩余天数”的计算口径（节点本地时区）。
- 预测公式与告警触发条件（含阈值单位 GB、防抖周期、是否发恢复邮件）。
- SMTP 配置项与默认收件人。
- Hub 部署位置（公网可达性）与是否前置 HTTPS。

## 5. 数据模型（目标态，Hub 数据库）

- `meta(key, value)`：schema 版本。
- `nodes(id, name, host, token_hash, reset_day, threshold_bytes, alert_email, tz_offset_minutes, last_seen_ts, last_cycle_start_ts, last_rx_bytes, last_tx_bytes, created_ts, updated_ts)`
- `samples(id, node_id, ts, cycle_start_ts, rx_bytes, tx_bytes, interfaces_json, raw_json)`：原始上报。
- `daily_usage(node_id, day, rx_bytes, tx_bytes, total_bytes)`：每日聚合。
- `alerts(id, node_id, ts, predicted_total_bytes, threshold_bytes, avg_daily_bytes, days_left, projected_exceed_day, status, detail)`：告警记录。

## 6. API（目标态，Hub）

- `GET /health`：健康检查。
- `GET /`：Dashboard 页面。
- `POST /api/report`：Agent 上报。
- `GET /api/status`：公开摘要 + 管理员标记。
- `POST /api/login`、`POST /api/logout`：管理员登录。
- `GET/POST /api/nodes`、`GET/POST /api/nodes/<id>`：节点管理（管理员）。
- `GET /api/nodes/<id>/stats`：单节点 7 日统计。
- `GET/POST /api/smtp`：SMTP 配置（管理员）。
- `POST /api/test-email`：发送测试邮件（管理员）。
- `GET /api/alerts`：告警历史（管理员）。

## 7. 已确认决策（2026-08-30）

- 传输默认 HTTP，不强制 HTTPS；生产可自行前置反代。
- SMTP 授权码仅写入运行时配置，不入库、不提交 git、不写日志。
- 阈值单位 GB，内部存字节，`0` 表示未启用。
- “日”统一默认北京时间（UTC+8）。
- 恢复邮件默认开启，可配置关闭。
