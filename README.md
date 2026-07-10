# VPS Traffic Monitoring

一个轻量的 VPS 流量统计面板，用来查看当前账单周期内的下行、上行和总流量，并支持设置每月某日自动重新开始统计。

## 特性

- 查看本周期下行流量、上行流量、总流量
- 查看实时下行速度和上行速度
- 支持指定每月 1-31 日自动重置统计周期
- 支持网页上手动重置当前周期
- 支持自动统计主要公网网卡，也支持指定网卡
- 使用 SQLite 保存增量数据，服务重启或 VPS 重启后不会丢失已统计流量
- 不依赖 vnStat、Docker 或数据库服务
- 提供 systemd 服务、交互式安装/更新/卸载脚本

## 一键命令

运行下面命令后，会弹出菜单：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/rainbowgag/VPS-Traffic-Monitoring/main/install.sh)
```

菜单选项：

```text
1. Install traffic monitor
2. Update traffic monitor
3. Uninstall traffic monitor
```

选择 `1` 安装或 `2` 更新时，会继续让你输入：

- Web 面板端口，直接回车默认 `8899`
- 每月流量重置日期，直接回车默认 `1`
- 统计网卡，直接回车自动识别主要网卡
- 是否导入当前网卡已累计流量，直接回车默认不导入

安装后打开：

```text
http://你的VPS_IP:端口
```

如果端口直接回车，就是：

```text
http://你的VPS_IP:8899
```

## 非交互安装

如果你想直接指定参数，也可以这样：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/rainbowgag/VPS-Traffic-Monitoring/main/install.sh) --action install --port 8899 --reset-day 10
```

如果要把当前网卡已经累计的流量导入本周期：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/rainbowgag/VPS-Traffic-Monitoring/main/install.sh) --action install --port 8899 --reset-day 10 --import-current
```

更新：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/rainbowgag/VPS-Traffic-Monitoring/main/install.sh) --action update --port 8899 --reset-day 10
```

卸载：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/rainbowgag/VPS-Traffic-Monitoring/main/install.sh) --action uninstall
```

彻底删除配置和数据库：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/rainbowgag/VPS-Traffic-Monitoring/main/install.sh) --action uninstall --purge
```

## 指定统计网卡

默认会自动排除 `lo`、Docker、bridge、veth、WireGuard、Tailscale 等常见虚拟网卡，只统计主要真实网卡。

如果你的 VPS 网卡名是 `eth0`：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/rainbowgag/VPS-Traffic-Monitoring/main/install.sh) --action install --interfaces eth0
```

多个网卡用英文逗号分隔：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/rainbowgag/VPS-Traffic-Monitoring/main/install.sh) --action install --interfaces eth0,ens3
```

## 常用命令

```bash
systemctl status vps-traffic-monitor
journalctl -u vps-traffic-monitor -f
systemctl restart vps-traffic-monitor
```

配置文件：

```text
/etc/vps-traffic-monitor/config.json
```

数据库：

```text
/var/lib/vps-traffic-monitor/traffic.db
```

## 准确性说明

程序直接读取 Linux `/proc/net/dev` 的网卡累计字节数，并按采样间隔保存增量到 SQLite。

这意味着：

- 程序重启后，已累计的历史流量仍然保留
- VPS 重启导致网卡计数器归零时，程序会检测到计数器回退，并从新的计数器继续累计
- 到达设置的每月重置日时，程序会自动创建新的统计周期
- 如果设置为每月 `31` 日，在没有 31 日的月份会自动使用当月最后一天
- 如果安装时选择导入当前网卡已累计流量，程序会把当时 `/proc/net/dev` 的当前累计值加入本周期，并把它设置为后续采样基准，避免启动后重复计算
- 如果当前周期已经有流量数据，再次选择导入会自动跳过，避免重复累加

注意：如果服务长时间停止，停止期间产生的流量无法采样到。建议保持 systemd 服务常驻运行。
