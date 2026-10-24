# ddns6fw · 锐捷 Reyee 路由器 IPv6 防火墙规则自动同步

> 一个零第三方依赖的小工具：自动读取 Linux 主机的公网 IPv6 地址，并把它同步到锐捷/Reyee 路由器 `access_ctrl` 防火墙规则的目标地址中，让通过 IPv6 暴露的 80/443 等服务在运营商前缀变化后仍能访问。

项目在代码中使用的内部名是 `ddns6fw`，目录名为 `ddns_WI-FI`。

## 功能

- 从 Linux 的 `/proc/net/if_inet6` 读取 IPv6 地址，无需安装 `iproute2`
- 支持按网卡名、IPv6 后缀（例如 `::1001`）筛选地址
- 自动排除临时地址、ULA、链路本地地址、已废弃/未完成 DAD 的地址
- 登录锐捷/Reyee 路由器，读取 `access_ctrl` 防火墙规则
- 匹配符合条件的规则（IPv6 + 源 `wan` + 动作 `ACCEPT` + 目标端口在列表中）
- 只更新规则的 `destIP`，不修改其它字段
- 状态持久化：记录上次成功的 IPv6 和最近一次同步结果
- 内置轻量 Web 配置界面（标准库 `http.server`）
- Docker 一键运行脚本，使用 `host` 网络模式以读取宿主机 IPv6

## 工作流程

```text
┌──────────────┐   读取 /proc/net/if_inet6    ┌──────────────┐
│   Linux 主机  │ ───────────────────────────> │   ipv6.py    │
└──────┬───────┘                              └──────┬───────┘
       │                                             │ 选出公网 IPv6
       │                                             v
┌──────┴───────┐   登录 /api/auth（GibberishAES）┌──────────────┐
│  router.py   │ <────────────────────────────── │  main.py     │
│  Reyee API   │   读取/更新 access_ctrl          │  同步循环     │
└──────┬───────┘                                └──────┬───────┘
       │                                               │
       v                                               v
┌──────────────┐                                 ┌──────────────┐
│  路由器防火墙  │                                 │  config.json │
│  规则 destIP  │                                 │  state.json  │
└──────────────┘                                 └──────────────┘
```

1. 读取 `config.json`（首次启动自动创建默认配置）。
2. `RouterClient` 请求 `https://<路由器>/cgi-bin/luci/`，从登录页提取 `GibberishAES.enc` 的 AES 密钥。
3. 用 `GibberishAES` 兼容算法加密路由器密码，调用 `/cgi-bin/luci/api/auth` 登录，获取 `sid`。
4. 通过 `/cgi-bin/luci/api/cmd` 读取 `access_ctrl` 规则。
5. 从当前主机选择可用 IPv6；优先复用上次成功的地址（按接口标识匹配），否则使用规则中已有的目标地址作为参考。
6. 对匹配到的规则更新 `destIP`，写回路由器；写入 `state.json`。
7. Web UI 展示状态，可修改配置、手动触发一次同步。

## 文件结构

| 文件 | 作用 |
| --- | --- |
| `main.py` | 程序入口、同步循环、规则匹配与更新 |
| `config.py` | `config.json` / `state.json` 的原子读写与默认配置 |
| `ipv6.py` | 解析 `/proc/net/if_inet6`、选择可用公网 IPv6 |
| `gibberish.py` | GibberishAES / OpenSSL AES-256-CBC 兼容加密，用于路由器登录 |
| `router.py` | 锐捷 Reyee 路由器 HTTP 客户端 |
| `webui.py` | 标准库 Web 配置界面与 JSON API |
| `selftest.py` | 本地/路由器自测脚本 |
| `run.sh` | Docker 启动、停止、日志、单次执行脚本 |

## 环境要求

- 一台运行 Linux、能访问公网 IPv6 的主机（或 NAS/软路由）
- 主机存在 `/proc/net/if_inet6`
- 锐捷/Reyee 路由器，且开启了可通过 IPv6 访问的防火墙规则
- Python **3.8+**（Docker 脚本使用 `python:3.12-slim`）
- 无需安装任何第三方 Python 库

> Docker 必须使用 `--network host`，否则容器看不到宿主机的 IPv6 地址。

## 快速开始

### 方式一：直接运行

```bash
# 1. 进入项目
cd ddns6fw

# 2. 启动（首次启动会生成 config.json）
python3 main.py

# 3. 打开 Web 配置界面
#    http://<主机IP>:6060
```

### 方式二：Docker / 一台 Debian 宿主机

```bash
chmod +x run.sh

./run.sh          # 后台启动，自动重启
./run.sh debug    # 前台运行，便于排错
./run.sh once     # 只同步一次
./run.sh logs     # 查看日志
./run.sh stop     # 停止并删除容器
./run.sh shell    # 进入容器
./run.sh check    # 检查代码是否完整、能否编译
```

Web 界面默认监听 `0.0.0.0:6060`，可通过 `http://<主机IP>:6060` 打开。

## 配置

配置保存在项目目录的 `config.json`，首次启动自动创建。可在 Web 界面填写，也可以手动编辑：

```json
{
  "router_host": "192.168.1.1",
  "router_password": "",
  "interface": "",
  "ipv6_suffix": "::1001",
  "ports": ["80", "443"],
  "interval": 60,
  "enabled": true,
  "web_host": "0.0.0.0",
  "web_port": 6060,
  "timeout": 15
}
```

| 字段 | 说明 |
| --- | --- |
| `router_host` | 路由器地址，例如 `192.168.1.1`；也可以带 `https://` |
| `router_password` | 路由器管理密码；**只保存在本机 `config.json`**，请勿提交到 Git |
| `interface` | 网卡名，例如 `eth0`；留空自动选择 |
| `ipv6_suffix` | IPv6 后缀，例如 `::1001`；留空则自动选择可用全局地址 |
| `ports` | 需要同步的防火墙规则目标端口列表 |
| `interval` | 同步间隔（秒），最小 10 |
| `enabled` | 是否启用自动同步 |
| `web_host` | Web UI 绑定地址，默认 `0.0.0.0` |
| `web_port` | Web UI 端口，默认 `6060` |
| `timeout` | 访问路由器的超时时间（秒） |

匹配规则条件：

```text
netProto == "ipv6"
src      == "wan"
target   == "ACCEPT"
destIP   非空
destPort 在 ports 列表中
```

程序只修改匹配规则的 `destIP`，其它字段保持原样。

## Web UI 与 API

Web UI 默认地址：`http://<主机IP>:6060`

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/` | 配置界面 |
| `GET` | `/api/status` | 当前配置、最近 IPv6、最近同步结果 |
| `GET` | `/api/config` | 当前配置（包含密码字段） |
| `POST` | `/api/config` | 保存配置；密码留空表示不修改 |
| `POST` | `/api/run` | 立即执行一次同步 |

> ⚠️ Web UI **没有登录认证**，且 `/api/config` 会返回路由器密码字段。请只在可信局域网内运行，不要直接暴露到公网；建议通过防火墙限制 6060 端口的访问范围，或使用反向代理增加认证。

## 测试

### 本地测试（不接触路由器）

```bash
python3 selftest.py
```

覆盖：

- AES-256 标准向量
- GibberishAES 与 OpenSSL/cryptography 的交叉验证
- `/proc/net/if_inet6` 解析
- 后缀、接口、临时地址、ULA、前缀变化下的地址选择
- `config.json` 持久化与 Web API
- 规则匹配和端到端更新流程（使用假的 IPv6 列表）

### 路由器测试（谨慎）

```bash
# 只读测试
python3 selftest.py --router --host 192.168.1.1 --password '<路由器密码>'

# 读写测试：会临时新增一条“已禁用”规则，测试后自动删除
python3 selftest.py --router --write --host 192.168.1.1 --password '<路由器密码>'
```

`--write` 会新增一条临时禁用规则，并在结束时恢复 80/443 规则的原始 `destIP`；仍建议在低风险时间执行，并先备份路由器配置。

## 已知限制与安全说明

- 项目通过解析 Reyee 登录页中的 `GibberishAES.enc(...)` 获取 AES 密钥；**不同固件版本可能不兼容**，登录失败时请检查登录页是否仍使用该格式。
- 路由器 HTTPS 默认使用自签名证书，`router.py` 中默认关闭 TLS 校验；请只在可信局域网使用，避免在不可信网络中运行。
- 路由器管理用户名固定为 `admin`；如你的路由器使用其它管理员账号，需要修改 `router.py`。
- 路由器密码以明文保存在本机 `config.json`；请设置文件权限（例如 `chmod 600 config.json`），不要把 `config.json` 提交到 Git。
- Web UI 无认证，且可读取配置中的密码；不要把它暴露到公网。
- 本工具会修改路由器防火墙规则，请先在测试环境确认匹配条件，并做好配置备份。
- 运营商 IPv6 前缀会变化；程序优先用接口标识（后 64 位）匹配上次地址，以提高前缀变化后的稳定性。

## 常见问题

**Q: 启动后 Web 页面打不开？**  
检查 `web_port` 是否被占用，以及主机防火墙是否放行 6060。

**Q: IPv6 选不到？**  
确认宿主机有全局 IPv6 地址；检查 `interface` 和 `ipv6_suffix`；查看 `/proc/net/if_inet6` 是否有 `scope 00` 的地址。

**Q: 登录路由器失败？**  
检查路由器地址、密码、管理员用户名，以及登录页源码中是否仍包含 `GibberishAES.enc`；密码错误次数过多时路由器可能临时锁定。

**Q: 规则没有更新？**  
检查匹配条件（IPv6、src=wan、target=ACCEPT、destPort 在配置列表中）以及规则是否已有 `destIP`。

## License

GNU Affero General Public License v3.0 or later（AGPL-3.0-or-later）。

详见 [LICENSE](LICENSE)。

## 免责声明

本项目按“现状”提供，不附带任何担保。修改路由器防火墙规则可能影响网络访问，请自行评估风险并做好备份。
