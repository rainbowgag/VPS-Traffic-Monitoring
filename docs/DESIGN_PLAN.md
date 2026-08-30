# VPS-Traffic-Monitoring 探针化 + 阈值预警 设计计划书

> 状态：用户已确认。阶段 1-6 已完成：Hub 数据层/上报、Agent 上报线程、每日聚合与 7 日 Dashboard、阈值预测与告警落库、SMTP 邮件告警，以及安装脚本与真机（38.58.59.103）联调验收。
> 目标：在保留原单机流量统计面板全部功能的前提下，将项目扩展为“主控 Hub + 探针 Agent”的多 VPS 集中流量监控，并加入近 7 日统计、按节点阈值预测、超限邮件提醒。

## 1. 项目背景与定位

原项目是一个轻量 VPS 流量统计面板：单机部署，直接读取 `/proc/net/dev`，用 SQLite 保存本周期增量，提供本地面板、网卡明细、重置日、历史周期、手动录入。

改造后定位：**多 VPS 集中式流量监控与阈值预警系统**。每个 VPS 安装探针（Agent），一台主机安装主控（Hub）；Hub 汇总所有节点流量，展示近 7 日统计，并对每个节点按流量阈值做“预计超限”预测，超限前邮件提醒。

## 2. 总体架构

```text
┌──────────────────────────┐        ┌────────────────────────────────────┐
│ 各 VPS：Agent (monitor.py) │        │ Hub (hub.py)                       │
│  - 采集 /proc/net/dev     │  push  │  - 接收上报、鉴权                   │
│  - 本地面板(原功能保留)    │ ─────> │  - daily_usage 每日聚合             │
│  - SQLite 本地落库         │ HTTPS  │  - 7 日统计 Dashboard               │
│  - 周期累计上报            │        │  - 阈值预测 + 邮件告警              │
└──────────────────────────┘        └────────────────────────────────────┘
```

- **Agent**：即原 `monitor.py`，默认行为不变；配置 `hub_url` 后新增上报线程。
- **Hub**：新增 `hub.py`，独立数据库与独立 systemd 服务。
- **通信**：Agent 推送到 Hub 的 `POST /api/report`，使用每节点 token 鉴权；生产环境建议用 Caddy/Nginx/Cloudflare 提供 HTTPS。

## 3. 与原功能的关系（保留项）

原功能全部保留，Agent 模式下依然可用：

- 公开页本周期下行/上行/总流量；
- 登录后的网卡明细、重置日、统计网卡、历史周期；
- 手动录入已用流量、手动重置；
- SQLite 本地增量、重启不丢、网卡计数归零自愈；
- systemd 服务与交互式安装/更新/卸载。

Hub 是**新增**的集中视图，不替代 Agent 本地面板。

## 4. 数据模型（Hub 数据库 hub.db）

- `meta(key TEXT PRIMARY KEY, value TEXT)`：schema 版本等元信息。
- `nodes`：
  - `id INTEGER PK`
  - `name TEXT`：显示名
  - `host TEXT`：标识/备注
  - `token_hash TEXT UNIQUE`：`sha256(agent_token)`
  - `reset_day INTEGER DEFAULT 1`
  - `threshold_bytes INTEGER DEFAULT 0`：0 表示未启用阈值
  - `alert_email TEXT`：可覆盖全局收件人
  - `tz_offset_minutes INTEGER DEFAULT 0`
  - `last_seen_ts INTEGER`：在线判断
  - `last_cycle_start_ts INTEGER`、`last_rx_bytes INTEGER`、`last_tx_bytes INTEGER`：计算相邻两次上报增量
  - `created_ts INTEGER`、`updated_ts INTEGER`
- `samples`（原始上报，用于排查/回填）：
  - `id INTEGER PK`、`node_id INTEGER`、`ts INTEGER`、`cycle_start_ts INTEGER`、`rx_bytes INTEGER`、`tx_bytes INTEGER`、`interfaces_json TEXT`、`raw_json TEXT`
- `daily_usage`（每日聚合，7 日统计与预测的数据源）：
  - `node_id INTEGER`、`day TEXT`（节点本地日 `YYYY-MM-DD`）、`rx_bytes INTEGER`、`tx_bytes INTEGER`、`total_bytes INTEGER`
  - `PRIMARY KEY(node_id, day)`
- `alerts`（告警记录，用于防抖与历史）：
  - `id INTEGER PK`、`node_id INTEGER`、`ts INTEGER`、`predicted_total_bytes INTEGER`、`threshold_bytes INTEGER`、`avg_daily_bytes INTEGER`、`days_left INTEGER`、`projected_exceed_day TEXT`、`status TEXT`、`detail TEXT`

## 5. Agent → Hub 上报协议

`POST /api/report`，请求体：

```json
{
  "token": "<agent_token>",
  "hostname": "vps-hk-01",
  "ts": 1750000000,
  "tz_offset_minutes": 480,
  "reset_day": 1,
  "cycle": {"start_ts": 1750000000, "rx_bytes": 123456, "tx_bytes": 654321},
  "interfaces": [{"name": "eth0", "rx_bytes": 123456, "tx_bytes": 654321}],
  "rates": {"rx_bps": 1024, "tx_bps": 2048}
}
```

Hub 处理：

1. 用 `token_hash` 定位节点；无效 token 返回 403。
2. 更新 `nodes.last_seen_ts`。
3. 若 `cycle.start_ts` 与 `last_cycle_start_ts` 相同：增量 = 本次累计 - 上次累计；否则视为新周期/重启，增量记 0 并重置基线。
4. 增量累加到 `daily_usage` 中“节点本地日”对应行。
5. 保存原始上报到 `samples`。

## 6. 近 7 日统计

- 取节点本地时区下最近 7 个自然日（含今天）的 `daily_usage` 汇总，前端以柱状图展示（内嵌 SVG/Canvas，不依赖 CDN）。
- 展示每节点的下行、上行、合计，以及在线状态、本周期已用、当前速率。

## 7. 阈值预测与告警

### 7.1 输入
- `threshold_bytes`：节点阈值（界面按 GB 输入，存字节）。
- `reset_day`：节点流量重置日。
- `current_total = cycle.rx_bytes + cycle.tx_bytes`：本周期已用。
- 近 7 日 `daily_usage`。

### 7.2 公式
```text
avg_daily      = sum(近 7 日 total_bytes) / min(7, 有数据天数)
days_left      = 从节点本地“今天”到下一个重置日之间的剩余天数（含今天）
projected      = current_total + avg_daily * days_left
projected_exceed_day = 预计累计超过阈值的节点本地日期
```

### 7.3 触发条件
- `threshold_bytes > 0` 且 `projected > threshold_bytes`：发送“预计超限”邮件。
- 若 `current_total` 已超过阈值：邮件措辞改为“已超限”。
- 可配置 `alert_cooldown_hours`（默认 24）：同一节点同一状态在冷却期内不重复发送。
- 可选（默认开启）：状态从“超限/预计超限”回到“安全”时发送恢复邮件。

### 7.4 邮件
- 全局 SMTP 配置；节点可设 `alert_email` 覆盖。
- 邮件内容：节点名、本周期已用、近 7 日均值、预计重置日前总量、阈值、剩余天数、预计超限日期、Dashboard 链接。
- 使用标准库 `smtplib` + `EmailMessage`，支持 SSL/TLS/STARTTLS；发送失败记录日志并在下一轮重试，不阻塞采集。

## 8. Hub Web / API

- 公开 Dashboard：节点卡片列表（名称、在线、本周期已用、7 日柱状图、告警标记）。
- 管理员：登录后管理节点（新增/编辑/删除、生成 token、设阈值、设重置日、设收件人）、配置 SMTP、发送测试邮件、查看告警历史。
- API 见 `ARCHITECTURE.md` 第 6 节。

## 9. 安全

- Agent token：`secrets.token_urlsafe(32)` 生成，Hub 只存 SHA-256；Agent 配置存明文（权限 0600）。
- 管理员密码：沿用原 PBKDF2 + session cookie 机制。
- 生产建议前置 HTTPS；如需要，后续可加入内置 `ssl` 证书支持。
- 日志不输出 token/密码。

## 10. 安装与部署

- `install.sh` 扩展角色：
  - `--action install`（默认，Agent，与原一致）
  - `--action install-hub`（Hub）
  - 非交互参数新增：`--role agent|hub`、`--hub-url`、`--agent-token`、SMTP 相关参数。
- systemd 服务：
  - `vps-traffic-monitor.service`（Agent，原服务名不变）
  - `vps-traffic-hub.service`（Hub）
- 配置文件：
  - Agent：`/etc/vps-traffic-monitor/config.json`（新增 `hub_url`、`agent_token`、`report_interval`）
  - Hub：`/etc/vps-traffic-hub/config.json` 与 `/var/lib/vps-traffic-hub/hub.db`

## 11. 兼容性与迁移

- `monitor.py` 中 `hub_url` 为空时，上报线程不启动，行为与当前版本一致。
- 原 Agent 数据库 schema 不变；Hub 使用独立数据库。
- 老用户升级：`install.sh --action update` 自动更新脚本，不丢原数据。

## 12. 测试方案（含测试机 38.58.59.103）

- 阶段 A：本地单测（数据层、预测算法、增量计算、邮件构造）。
- 阶段 B：使用测试机 `38.58.59.103`（root / 22 / 已给凭据）：
  1. 先用只读命令确认可登录与系统环境（`uname -a`、`python3 --version`、`ip -br addr`）。
  2. 部署 Agent（或临时进程）到测试机，上报到同一台机器上临时启动的 Hub（`http://127.0.0.1:8898`），验证采集-上报-落库链路。
  3. 用 `curl`/`iperf3` 制造下行流量，验证 7 日聚合与实时速率。
  4. 将阈值设为极小值触发告警，验证预测与邮件（用可用的测试 SMTP 或本地日志替代）。
- 凭据使用原则：仅用于用户授权的测试；不在日志/提交中留存。

## 13. 风险与待确认事项

1. **Hub 部署位置**：Agent 需要能访问到 Hub；放在公网 VPS 还是其中一台机器？是否前置 HTTPS？
2. **SMTP 凭证**：发件服务器、端口、账号、授权码、默认收件人（未提供前，先用日志/告警表验证逻辑）。
3. **阈值默认单位与默认值**：建议按 GB 输入，默认 0=未启用。
4. **“日”的切分时区**：建议采用节点本地时区（Agent 上报偏移量）。
5. **是否发恢复邮件**：建议开启，可配置。
6. **是否只统计总流量，还是保留每节点每网卡明细**：建议 Hub 至少保存并展示每节点合计 + 每接口明细（来自上报的 `interfaces`）。

---

## 已确认决策（2026-08-30）

1. **传输**：默认先使用 HTTP，不强制 HTTPS；生产可自行前置 Caddy/Nginx/Cloudflare。
2. **SMTP 凭证**：使用用户提供的 16 位授权码；**仅写入运行时配置文件，不进入 git 仓库、不写日志**。发件邮箱地址与收件邮箱地址在阶段 5 联调前补齐。
3. **阈值单位**：界面按 GB 输入，内部存字节；`0` 表示未启用阈值。
4. **“日”的切分**：统一默认北京时间（UTC+8）；Agent 上报时区偏移，未上报时默认 `480` 分钟。
5. **恢复邮件**：默认开启“预计超限”告警；状态回到安全后发送恢复邮件，并提供开关可配置关闭。




