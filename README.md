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
- 提供 systemd 服务、安装脚本和卸载脚本

## 一键安装

默认端口是 `8088`，默认每月 `1` 日重置：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/rainbowgag/VPS-Traffic-Monitoring/main/install.sh)
```

安装后打开：

```text
http://你的VPS_IP:8088
```

## 指定端口和每月重置日

例如使用 `8899` 端口，每月 `10` 日自动重置：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/rainbowgag/VPS-Traffic-Monitoring/main/install.sh) --port 8899 --reset-day 10
```

## 指定统计网卡

默认会自动排除 `lo`、Docker、bridge、veth、WireGuard、Tailscale 等常见虚拟网卡，只统计主要真实网卡。

如果你的 VPS 网卡名是 `eth0`：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/rainbowgag/VPS-Traffic-Monitoring/main/install.sh) --interfaces eth0
```

多个网卡用英文逗号分隔：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/rainbowgag/VPS-Traffic-Monitoring/main/install.sh) --interfaces eth0,ens3
```

## 升级或重新配置

重复运行安装命令即可。脚本会覆盖程序文件、更新配置并重启服务，但不会删除已有流量数据库。

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/rainbowgag/VPS-Traffic-Monitoring/main/install.sh) --port 8088 --reset-day 1
```

## 卸载

默认卸载程序和 systemd 服务，但保留配置与流量数据库：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/rainbowgag/VPS-Traffic-Monitoring/main/uninstall.sh)
```

彻底删除配置和数据库：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/rainbowgag/VPS-Traffic-Monitoring/main/uninstall.sh) --purge
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

注意：如果服务长时间停止，停止期间产生的流量无法采样到。建议保持 systemd 服务常驻运行。
