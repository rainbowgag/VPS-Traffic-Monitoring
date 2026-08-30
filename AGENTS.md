# AGENTS.md

## 项目定位
VPS 流量监控与阈值预警系统：在保留原单机流量统计面板全部功能的基础上，扩展为“主控 Hub + 探针 Agent”的多 VPS 集中监控。面向自建 VPS 用户，查看各节点本周期 / 近 7 日流量，并在按当前进度预计流量重置日前会超过阈值时邮件提醒。

## 技术栈
- Python 3，以标准库为主：`http.server` / `sqlite3` / `smtplib` / `threading` / `hmac` / `secrets`
- Bash 安装脚本 + systemd 部署
- SQLite 本地存储
- 前端为内嵌单页 HTML/JS，不依赖 CDN、离线可用
- 无第三方运行时依赖；不引入 vnStat、Docker、外部数据库服务

## 运行 / 构建 / 测试
- 探针（原单机程序，保留全部原功能）：
  - Linux：`python3 monitor.py --config /etc/vps-traffic-monitor/config.json`
  - 本地开发：`python3 monitor.py --config monitor.dev.json`
- 主控：
  - Linux：`python3 hub.py --config /etc/vps-traffic-hub/config.json`
  - 本地开发：`python3 hub.py --config hub.dev.json`
- 语法检查：`python3 -m py_compile monitor.py hub.py`
- 单元测试（标准库 unittest）：`python3 -m unittest discover -s tests -p "test_*.py"`
- 安装/更新/卸载：`bash install.sh`

## 代码约定
- 用户可见文案使用中文；代码注释可使用中文。
- 优先标准库、单文件实现，保持轻量；新功能必须向后兼容。
- 配置用 JSON，数据用 SQLite；敏感信息不写日志、不提交 git。
- 原功能必须保留：本地面板、网卡明细、重置日、历史周期、手动录入已用流量。
- 每次改动后运行 `py_compile` 与 `unittest`。

## 禁用事项
- 禁止引入 Flask/Django 等 Web 框架、外部数据库服务、Docker 依赖。
- 禁止删除或弱化原单机功能。
- 禁止把 admin 密码、agent token、SMTP 密码写入日志或提交到仓库。
