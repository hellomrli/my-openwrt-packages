# my-openwrt-packages

[![Sync OpenWrt Packages](https://github.com/hellomrli/my-openwrt-packages/actions/workflows/sync-packages.yml/badge.svg)](https://github.com/hellomrli/my-openwrt-packages/actions/workflows/sync-packages.yml)

个人 OpenWrt / ImmortalWrt 第三方包镜像仓库，主要供 [`hellomrli/my-ImmortalWrt`](https://github.com/hellomrli/my-ImmortalWrt) 固件构建使用。

本仓库只维护一组明确需要的包：一部分从上游定时同步，一部分是本仓库本地维护的定制包。包同步不会主动触发固件编译，避免上游包频繁更新导致固件反复重编。

## 包分组

### 当前固件使用

| 包 | 路径 | 来源 | 说明 |
|----|------|------|------|
| `adguardhome-dual` | `adguardhome-dual` | 本地维护 | 双 AdGuardHome 运行时包，用于 `dnsmasq + daed + 双 ADH` DNS 分流结构。 |
| `dae` / `luci-app-daed` | `dae` | `QiuSimons/luci-app-daed` | Daed 后端和 LuCI 管理界面；固件内核配置需要 eBPF / BTF / XDP 支持。 |
| `luci-app-lucky` / `lucky` | `luci-app-lucky` | `gdy666/luci-app-lucky` | Lucky 运行时和 LuCI 管理界面。 |
| `luci-app-watchdog` / `watchdog` | `luci-app-watchdog` | `sirpdboy/luci-app-watchdog` | 登录 / SSH 失败登录防护。 |
| `golang` | `golang` | `sbwml/packages_lang_golang` | OpenWrt Go 打包目录，固定跟随 `26.x` 分支。 |

### 仅镜像保留

以下包会跟随上游同步，但不会自动编译进 `my-ImmortalWrt`；只有固件 `.config` 显式选择时才会参与构建。

| 包 | 路径 | 上游 |
|----|------|------|
| `luci-app-adguardhome` | `luci-app-adguardhome` | `rufengsuixing/luci-app-adguardhome` |
| `luci-app-mosdns` | `luci-app-mosdns` | `sbwml/luci-app-mosdns` |
| `luci-app-smartdns` | `luci-app-smartdns` | `pymumu/luci-app-smartdns` |
| `luci-app-vlmcsd` | `luci-app-vlmcsd` | `mchome/luci-app-vlmcsd` |
| `openclash` | `openclash` | `vernesong/OpenClash` |
| `openwrt-passwall` | `openwrt-passwall` | `Openwrt-Passwall/openwrt-passwall` |
| `openwrt-passwall2` | `openwrt-passwall2` | `Openwrt-Passwall/openwrt-passwall2` |

## Golang 说明

`golang/` 由 `sources.json` 同步 [`sbwml/packages_lang_golang`](https://github.com/sbwml/packages_lang_golang) 的 `26.x` 分支。

固件构建仍会在 `./scripts/feeds install -a` 之后，按 sbwml 的说明将 Go 包移动到 OpenWrt feed 的标准路径：

```sh
rm -rf package/my-openwrt-packages/golang
git clone --depth 1 https://github.com/sbwml/packages_lang_golang -b 26.x package/my-openwrt-packages/golang
rm -rf feeds/packages/lang/golang
mv package/my-openwrt-packages/golang feeds/packages/lang/golang
```

这样最终只保留 `feeds/packages/lang/golang` 这一份 Golang 包定义，避免 OpenWrt 官方 feed 与第三方 Golang 包并存冲突。

## 同步机制

同步源由 [`sources.json`](sources.json) 管理，同步脚本是 [`scripts/sync-packages.py`](scripts/sync-packages.py)：

```sh
python3 scripts/sync-packages.py
```

同步流程：

1. 读取 `sources.json`。
2. 浅克隆每个上游仓库。
3. 移除上游 `.git` 元数据和 workflow 文件。
4. 覆盖本仓库对应目录。
5. 重新生成 [`SYNCED_SOURCES.md`](SYNCED_SOURCES.md)，记录分支和 commit。

GitHub Actions 每 6 小时自动同步一次，也支持手动运行 `Sync OpenWrt Packages`。同步只更新本仓库，不会 dispatch 固件构建。

## 在 OpenWrt 中使用

在 OpenWrt / ImmortalWrt 源码树中克隆到 `package/` 目录下：

```sh
git clone --depth 1 https://github.com/hellomrli/my-openwrt-packages.git package/my-openwrt-packages
```

然后在固件 `.config` 中显式选择需要的包，例如：

```text
CONFIG_PACKAGE_daed=y
CONFIG_PACKAGE_luci-app-daed=y
CONFIG_PACKAGE_luci-app-lucky=y
CONFIG_PACKAGE_luci-app-watchdog=y
CONFIG_PACKAGE_adguardhome-dual=y
```

`daed` 需要内核启用 eBPF / BTF / XDP。当前 `my-ImmortalWrt` 配置保留 kernel BTF，并显式选择 `DAED_USE_KERNEL_BTF`。

## 本地维护包

### `adguardhome-dual`

该包安装两个 AdGuardHome 实例，不依赖 `luci-app-adguardhome`：

| 服务 | DNS 监听 | Web 监听 | 配置文件 |
|------|----------|----------|----------|
| `adh-direct` | `127.0.0.1:50530` | `192.168.50.1:50080` | `/etc/AdGuardHome-direct.yaml` |
| `adh-proxy` | `127.0.0.1:50531` | `192.168.50.1:50081` | `/etc/AdGuardHome-proxy.yaml` |

默认 YAML 模板不会提交现有路由器的真实 Web 登录密码哈希。全新刷机后请自行设置 Web 登录信息；保留配置升级时则由固件的 sysupgrade 保留规则继续保留现有 YAML。

## 维护规则

新增同步包时：

1. 在 `sources.json` 增加上游仓库、目标路径和说明。
2. 运行 `python3 scripts/sync-packages.py`。
3. 检查变更目录和 `SYNCED_SOURCES.md`。
4. 只有需要编译进固件时，才在 `my-ImmortalWrt` 的 `.config` 中启用。

新增本地维护包时，不要把路径加入 `sources.json`，否则下一次同步会覆盖本地改动。

## 安全规则

不要提交路由器私有运行时数据：

- 订阅地址
- Cookie、token、API key
- 私有证书或私钥
- 真实 AdGuardHome 密码哈希
- 设备运行时数据库

本仓库只应保存包源码、打包元数据和安全的默认模板。

## 本地检查

```sh
python3 -c "import ast, pathlib; ast.parse(pathlib.Path('scripts/sync-packages.py').read_text())"
python3 -m json.tool sources.json >/tmp/sources.json.check
```
