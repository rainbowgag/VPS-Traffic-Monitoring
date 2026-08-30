# VPS Traffic Monitoring

一个轻量的 VPS 流量监控项目，支持两种用法：

1. **单机模式**：每台 VPS 独立统计本机流量（原功能）。
2. **探针模式**：一台主控 Hub + 多台探针 Agent，集中查看多台 VPS 的近 7 日流量，并按阈值在“预计超限”时邮件提醒。

## 特性

- 单机模式保留全部原功能：本周期下行/上行/总流量、网卡明细、重置日、历史周期、手动录入、手动重置。
- 探针模式：Agent 主动上报，Hub 汇总多节点。
- 近 7 日流量统计（默认按北京时间 UTC+8 切“日”）。
- 每节点独立阈值（GB，`0` = 未启用），预测公式：
  `预计总流量 = 本周期已用 + 近 7 日日均 × 剩余天数`。
- 预计超限 / 已超限邮件提醒，恢复后可选发送恢复邮件。
- 纯 Python 标准库 + SQLite，不依赖 vnStat、Docker 或数据库服务。

## 架构

```text
VPS Agent (monitor.py)  --POST /api/report-->  Hub (hub.py)  -->  Dashboard + 邮件告警
```

- Agent：读取 `/proc/net/dev`，本地 SQLite 落库；配置 `hub_url` 后周期上报。
- Hub：接收上报、每日聚合、7 日统计、阈值预测、SMTP 告警。

## 一键安装：主控 Hub

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/rainbowgag/VPS-Traffic-Monitoring/main/install.sh) \
  --action install --role hub \
  --port 8898 \
  --admin-user admin --admin-password '你的密码' \
  --smtp-enabled 1 \
  --smtp-host smtp.gmail.com --smtp-port 587 \
  --smtp-username 'youyo3269@gmail.com' \
  --smtp-password '你的授权码' \
  --smtp-from 'youyo3269@gmail.com' \
  --smtp-to '708805226@qq.com' \
  --smtp-starttls 1
```

安装完成后：

```bash
# 新建节点，输出 agent token（只显示一次）
python3 /opt/vps-traffic-hub/hub.py --config /etc/vps-traffic-hub/config.json --add-node 香港VPS

# 给节点设置阈值（GB）、重置日、收件邮箱（收件邮箱可留空使用全局）
python3 /opt/vps-traffic-hub/hub.py --config /etc/vps-traffic-hub/config.json --set-node 1 --threshold-gb 1000 --reset-day 1 --email '708805226@qq.com'
```

Dashboard：

```text
http://你的HUB_IP:8898
```

## 管理页面

打开 Dashboard 后点击右上角“登录”，输入管理员用户名和密码：

- 添加 VPS：填写备注名称、主机/IP、重置日、阈值 GB、收件邮箱（可选），提交后生成一键安装命令。
- 节点管理：编辑备注名称/阈值/收件邮箱、重新生成安装命令、删除节点。
- SMTP 邮件设置：修改发件邮箱、收件邮箱、SMTP 主机/端口/账号，并可发送测试邮件。


## 一键安装：探针 Agent（每台被监控 VPS）

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/rainbowgag/VPS-Traffic-Monitoring/main/install.sh) \
  --action install \
  --hub-url 'http://你的HUB_IP:8898' \
  --agent-token '上一步生成的 token' \
  --report-interval 60 \
  --port 8899
```

Agent 安装后仍保留本机面板：`http://你的VPS_IP:8899`。

## 一键安装：单机模式（原用法）

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/rainbowgag/VPS-Traffic-Monitoring/main/install.sh)
```

交互式菜单：`1` 安装 / `2` 更新 / `3` 卸载。

非交互单机安装示例：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/rainbowgag/VPS-Traffic-Monitoring/main/install.sh) \
  --action install --port 8899 --reset-day 10 --admin-user admin --admin-password '你的密码'
```

## 手动录入已用流量

如果 VPS 已经用了半个月才安装监控：

1. 打开本机面板并登录。
2. 找到“手动录入已用流量”。
3. 输入已使用的下行、上行流量和单位，点击“添加到本周期”。

## 指定统计网卡

```bash
bash install.sh --action install --interfaces eth0,ens3
```

## 常用命令

```bash
# Agent
systemctl status vps-traffic-monitor
journalctl -u vps-traffic-monitor -f
systemctl restart vps-traffic-monitor

# Hub
systemctl status vps-traffic-hub
journalctl -u vps-traffic-hub -f
systemctl restart vps-traffic-hub
```

配置文件与数据库：

```text
Agent: /etc/vps-traffic-monitor/config.json
       /var/lib/vps-traffic-monitor/traffic.db
Hub:   /etc/vps-traffic-hub/config.json
       /var/lib/vps-traffic-hub/hub.db
```

## 本地开发

```bash
# 依赖：Python 3 标准库，无第三方包
python3 -m py_compile monitor.py hub.py
python3 -m unittest discover -s tests -p "test_*.py"
python3 hub.py --config hub.dev.json
python3 monitor.py --config monitor.dev.json   # 需 Linux /proc/net/dev
```

## 准确性说明

Agent 直接读取 Linux `/proc/net/dev` 的网卡累计字节数，按采样间隔保存增量到 SQLite，并按周期上报给 Hub：

- 服务重启后历史流量仍保留。
- VPS 重启导致网卡计数器归零时，会检测到回退并从新计数器继续累计。
- 到达每月重置日时自动创建新周期；`31` 日在无 31 日的月份自动取当月最后一天。
- 若服务长时间停止，停止期间的流量无法采样，建议保持 systemd 服务常驻。

> 安全提示：admin 密码、agent token、SMTP 授权码只应写入运行时配置文件，不要提交到仓库或写入日志。
