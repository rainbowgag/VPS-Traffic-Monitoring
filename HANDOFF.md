# HANDOFF.md

## 交接块
- **Stopped here**：阶段 7 已完成——Hub 增加管理员登录、网页端节点管理（添加 VPS 生成一键安装命令、备注名称、编辑/删除、重新生成命令）与 SMTP 收件邮箱自定义；28 个单测通过，登录/鉴权/建节点/管理接口端到端验证通过。
- **Next**：可选——推送本地分支到 GitHub（生成的一键命令依赖 GitHub raw 的最新 install.sh），或继续增强（HTTPS、更多节点字段、邮件模板等）。
- **Blocker**：推送 GitHub 需你确认/授权；推送前，网页生成的一键安装命令在全新 VPS 上会因 GitHub 仍是旧脚本而不可用。

## 最近完成
- 阶段 1-6：见 git 历史（Hub/Agent、7 日统计、阈值预测、SMTP 邮件、安装脚本与真机联调）。
- 阶段 7：
  - Hub 管理员登录：PBKDF2 密码 + HMAC session cookie。
  - 节点表新增 `token` 明文列（用于生成安装命令），并做旧库迁移。
  - 节点管理 API：`POST /api/nodes`、`/api/nodes/<id>/update|delete|token`。
  - SMTP 管理 API：`/api/smtp`、`/api/test-email`。
  - `/api/status` 对管理员返回 token、阈值、收件邮箱与 SMTP 配置（公开视图不含敏感信息）。
  - Dashboard 重写：登录面板、添加 VPS 表单、一键安装命令、节点管理表、SMTP 设置、测试邮件。
  - 新增 `tests/test_hub_auth.py`；`unittest` 共 28 个用例全部通过。
  - 本地 HTTP 端到端：未登录建节点返回 403、登录后建节点返回命令、管理员 status 含 token/SMTP 均通过。
- 注意：生成的安装命令使用 GitHub raw 地址，需推送最新 `install.sh` 到 `main` 后才可在新 VPS 上使用。
