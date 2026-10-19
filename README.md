# Brook 端口转发管理脚本

[![Tests](https://github.com/LianZiZhou/brook-pf/actions/workflows/test.yml/badge.svg)](https://github.com/LianZiZhou/brook-pf/actions/workflows/test.yml)

用于管理 Linux 上的 Brook TCP/UDP 端口转发。替代 Toyo 旧版 `brook-pf.sh` 的安装与服务管理方式，兼容旧 `brook.conf`，支持全新安装及自动迁移。

## 安装或迁移旧安装

**在目标 Linux 服务器执行。新装和旧版迁移使用同一条命令：**

```bash
curl -fL --retry 3 https://raw.githubusercontent.com/LianZiZhou/brook-pf/main/brook-pf.sh -o brook-pf.sh && sudo bash brook-pf.sh install
```

也可使用 wget，保持 HTTPS 证书校验：

```bash
wget -O brook-pf.sh https://raw.githubusercontent.com/LianZiZhou/brook-pf/main/brook-pf.sh && sudo bash brook-pf.sh install
```

进入中文管理菜单：

```bash
sudo bash /usr/local/brook-pf/brook-pf.sh
```

从 v2.0.2 起，主菜单恢复原版的颜色、分隔线、单列布局、编号和状态提示：

```text
  Brook 端口转发 一键管理脚本 [v2.0.3]
  ---- LianZiZhou | github.com/LianZiZhou/brook-pf ----

  0. 升级脚本
————————————
  1. 安装 Brook
  2. 更新 Brook
  3. 卸载 Brook
————————————
  4. 启动 Brook
  5. 停止 Brook
  6. 重启 Brook
————————————
  7. 设置 Brook 端口转发
  8. 查看 Brook 端口转发
  9. 查看 Brook 日志
 10. 监控 Brook 运行状态
————————————
```

`7` 的子菜单保持原版四项：添加、删除、修改、启用/禁用；保留连续添加/删除、端口及目标确认、默认取消和启用状态提示。`8` 按原版带标签显示规则。高级监听地址仍可通过命令行 `--listen` 设置，菜单修改已有规则会保留其监听地址。

`0` 升级管理脚本（不会更新 Brook 二进制或主动重启转发），下载正式发布附件并验证 SHA256，同时更新当前脚本和已安装副本。`10` 保留原版监控开关及确认方式，底层使用 systemd 的重启策略；关闭监控不会停止正在运行的转发，设置在重启服务器后仍保留。主动执行“停止 Brook”会保持停止状态。

全新安装默认没有转发规则，服务会等待添加规则。安装会启用开机启动；迁移会短暂中断现有连接。管理脚本本身不会创建代理账户，也不需要 Brook 密码。

## 系统要求

- Linux，使用 systemd 235 或更高版本作为 init。
- Python 3.6+；缺少 Python 时，安装入口会通过 apt/dnf/yum 安装 Python 3 和 CA 证书。
- Debian 10+、Ubuntu 20.04+、Rocky/AlmaLinux 8+、CentOS Stream 9+ 满足基本依赖要求。持续集成实际测试 Ubuntu 24.04 和 Rocky Linux 9。
- 支持 amd64、arm64、386、armv7、armv6 的官方发布包；实际可用性取决于所选 Brook 版本是否提供对应包及其内核要求。
- 不支持 CentOS 7、OpenWrt、非 systemd 容器或 macOS 服务安装。
- 服务器必须能访问 GitHub API 和 Releases。GitHub 限流、证书错误或下载失败会报错退出，不会自动切换第三方源或跳过校验。

如果 Python 已存在但缺少 CA 证书，请先用系统包管理器安装 `ca-certificates`。不要添加 `--no-check-certificate` 或 `curl -k`。

从 v2.0.1 起，脚本固定使用 UTF-8 输入输出，兼容 `C`/ASCII locale 及继承了 `PYTHONIOENCODING=latin-1` 的环境，无需修改服务器的系统语言设置。若 v2.0.0 安装时出现 `'latin-1' codec can't encode characters`，重新下载新版后执行 `install` 即可。

## 常用操作

以下命令均在目标服务器执行。可直接使用菜单，也可使用命令行：

```bash
# 本机 10000 端口 -> 目标服务器的 443 端口，TCP 和 UDP 同时转发
sudo bash /usr/local/brook-pf/brook-pf.sh add 10000 203.0.113.10 443

# 仅监听本机回环地址
sudo bash /usr/local/brook-pf/brook-pf.sh add 10001 example.com 443 --listen 127.0.0.1

# IPv6：目标地址及监听地址均可使用 IPv6
sudo bash /usr/local/brook-pf/brook-pf.sh add 10002 2001:db8::10 443 --listen ::

# 修改目标，保留本地端口；也可加 --new-port 10003 修改本地端口
sudo bash /usr/local/brook-pf/brook-pf.sh set 10000 example.com 8443

sudo bash /usr/local/brook-pf/brook-pf.sh disable 10000
sudo bash /usr/local/brook-pf/brook-pf.sh enable 10000
sudo bash /usr/local/brook-pf/brook-pf.sh delete 10000
sudo bash /usr/local/brook-pf/brook-pf.sh list
sudo bash /usr/local/brook-pf/brook-pf.sh check

sudo bash /usr/local/brook-pf/brook-pf.sh start
sudo bash /usr/local/brook-pf/brook-pf.sh stop
sudo bash /usr/local/brook-pf/brook-pf.sh restart
sudo bash /usr/local/brook-pf/brook-pf.sh status
sudo bash /usr/local/brook-pf/brook-pf.sh logs
```

`set` 会使用你提供的整条规则；省略 `--listen` 时恢复为 `0.0.0.0`，省略 `--disabled` 时启用规则。`add` 也支持 `--disabled`。

同一个本地端口只能配置一次。每条规则同时转发 TCP 和 UDP；默认监听所有 IPv4 接口。IPv6 的 `::` 是否同时接收 IPv4 取决于系统设置；需要双栈时请按实际环境验证。

## 防火墙

配置转发不会自动更改整机防火墙。原脚本创建的 iptables 规则会保留。

```bash
# 查看针对当前活动 firewalld/UFW 的放行命令
sudo bash /usr/local/brook-pf/brook-pf.sh firewall

# 实际放行单个端口的 TCP 和 UDP
sudo bash /usr/local/brook-pf/brook-pf.sh firewall 10000 --apply
```

firewalld 使用默认 zone，同时添加运行时和永久规则，不执行全局 reload；如果网卡位于其他 zone，请在相应 zone 中手动放行。未运行 firewalld/UFW 时，会提示按现有 nftables/iptables 策略配置。云服务器还需配置云安全组。

删除、禁用规则或卸载不会自动删除防火墙放行项，因为这些规则也可能由其他服务使用。

## 旧配置迁移

自动检测这些旧文件和进程：

- `/usr/local/brook-pf/brook.conf`
- `/usr/local/brook-pf/brook`
- `/etc/init.d/brook-pf` 及对应 SysV 开机链接
- root crontab 中旧脚本生成的 `brook-pf.sh monitor` 行

旧版四列格式可直接读取，启用/禁用状态保持不变：

```text
# 本地端口 目标地址 目标端口 启用状态
10000 203.0.113.10 443 1
10001 example.com 8443 0
```

新版保存为五列，新增监听 IP；缺失第五列的旧规则默认使用 `0.0.0.0`：

```text
10000 203.0.113.10 443 1 0.0.0.0
10001 example.com 8443 0 0.0.0.0
```

迁移先校验全部配置、下载并验证新程序，再保存旧文件备份，停用旧监控、停止旧进程、安装 systemd 服务。只终止可执行文件确认为旧安装路径且命令含 `relays` 的进程，不按全局进程名匹配其他 Brook 实例。原来的日志、时区、其他 cron 项目和防火墙规则保留。

旧配置存在重复端口、非法地址或格式问题时会中止，请先执行 `check` 修复。使用自定义路径、非 root cron、定制启动脚本或原生同名 systemd 单元的旧安装，需要先人工检查；不承诺自动迁移这些定制部署。

## 更新、备份及卸载

```bash
# 更新 Brook 至官方 latest 正式版本，保留服务的运行和开机启动状态
sudo bash /usr/local/brook-pf/brook-pf.sh update

# 指定版本（需支持 relay --from/--to，且发布资产带 SHA256）
sudo bash /usr/local/brook-pf/brook-pf.sh update v20270101

# 卸载前会询问；--yes 可用于自动化。配置和备份仍保留
sudo bash /usr/local/brook-pf/brook-pf.sh uninstall
```

`update` 更新 Brook 并安装当前执行的管理脚本。**更新管理脚本**可选择菜单 `0` 或运行 `sudo bash /usr/local/brook-pf/brook-pf.sh self-update`。从 v2.0.0/v2.0.1 升级时，先重新下载本仓库的 `brook-pf.sh`，再执行 `sudo bash brook-pf.sh self-update`；全新安装及旧版迁移请用 `install`。

所有下载使用系统 TLS 校验；安装包 SHA256 必须匹配 GitHub Releases 资产的 `digest`。没有摘要的旧发布需要传入你独立核实的 `--sha256 HEX`，不会静默降级为不校验。摘要校验能够发现文件不一致，并不代替上游发布者的签名。

备份保存在 `/var/backups/brook-pf/时间-随机标识/`，仅 root 可读。`manifest.json` 记录原始路径、备份文件编号、原始启停状态及迁移前的 root crontab。成功和失败操作都会保留备份；请按自己的保留周期清理。

下载/配置校验失败不会停止旧服务。安装或修改配置后，新服务启动失败会尝试恢复旧文件、配置、cron 和服务状态。自动恢复若失败，会明确输出备份目录；应停止重试并检查日志及备份。启动检查会确认每个 Brook 子进程已建立 TCP 和 UDP 监听，不代表远端地址可达，请另行检查业务流量。

修改规则会重启全部转发进程，已有连接可能中断。手工停止服务后修改规则只保存配置，不会自行启动。监控开启时，systemd 负责进程退出后的重启；60 秒内连续失败 5 次会停止重试，可用 `restart` 清除失败状态后重试。

## 开发与测试

`brook_pf.py` 是维护源文件，`scripts/build.py` 将其打包为独立的 `brook-pf.sh`。下载脚本不需要克隆仓库，也不需要 pip 依赖。

```bash
python3 scripts/build.py
python3 -m unittest discover -s tests -v
bash -n brook-pf.sh
```

集成测试**只能在可丢弃的 Linux systemd 容器/虚拟机执行**：它会安装、迁移和卸载测试用 Brook 服务，修改容器内的 root crontab。GitHub Actions 使用 Ubuntu 24.04 和 Rocky Linux 9 容器，校验官方 Brook 安装包，并检查 TCP/UDP、IPv6、配置回滚、二进制更新回滚、旧版迁移及卸载保留配置。

```bash
docker build -t brook-pf-test -f tests/Dockerfile.ubuntu .
docker run -d --name brook-pf-ci --privileged --cgroupns=private \
  --tmpfs /run --tmpfs /run/lock -v "$PWD:/src:ro" -w /src brook-pf-test
docker exec -e BROOK_PF_INTEGRATION=1 brook-pf-ci python3 tests/integration.py
docker rm -f brook-pf-ci
```

## 相关项目与许可

- [原版管理脚本：ToyoDAdoubiBackup/doubi](https://github.com/ToyoDAdoubiBackup/doubi/blob/master/brook-pf.sh)
- [Brook 上游](https://github.com/txthinking/brook)
- [Brook 官方发布](https://github.com/txthinking/brook/releases)

本管理脚本使用 MIT 许可证，保留原项目署名。Brook 可执行文件由官方 Releases 下载，不包含在本仓库中，其许可和使用条款以对应上游版本为准。本项目并非 Brook 官方项目。
