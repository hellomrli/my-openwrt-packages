# my-openwrt-packages

[![Sync OpenWrt Packages](https://github.com/hellomrli/my-openwrt-packages/actions/workflows/sync-packages.yml/badge.svg)](https://github.com/hellomrli/my-openwrt-packages/actions/workflows/sync-packages.yml)

个人 OpenWrt / ImmortalWrt 第三方包镜像仓库，供
[`hellomrli/my-ImmortalWrt`](https://github.com/hellomrli/my-ImmortalWrt) 固件构建使用。

## 这个仓库解决什么问题

固件构建原本在编译时直接克隆各个上游仓库。第三方 OpenWrt 插件仓库删库、改名、转私有或被
DMCA 下架都不算罕见，一旦发生，固件立刻无法编译，而且往往是在无人值守的定时构建里才发现。

本仓库每 6 小时把需要的上游包同步进来，构建改为从这里取包。上游消失时，镜像里仍有最后一次
成功同步的副本，固件照常能编译。

同步**不会**触发固件重编，避免上游包频繁更新导致固件反复重建。

## 包分组

### 当前固件使用

这三个包由 my-ImmortalWrt 的
[`.github/packages.json`](https://github.com/hellomrli/my-ImmortalWrt/blob/main/.github/packages.json)
声明，构建时按路径抽取。

| 包 | 路径 | 上游 | 说明 |
|----|------|------|------|
| `dae` / `daed` / `luci-app-daede` | `openwrt-daede` | `kenzok8/openwrt-daede` | dae 与 daed 双后端及统一 LuCI 管理界面；需要内核 eBPF / BTF / XDP 支持。 |
| `lucky` / `luci-app-lucky` | `luci-app-lucky` | `gdy666/luci-app-lucky` | Lucky 运行时和 LuCI 管理界面。 |
| `watchdog` / `luci-app-watchdog` | `luci-app-watchdog` | `sirpdboy/luci-app-watchdog` | 登录 / SSH 失败登录防护。 |

**dae 相关包只保留 `kenzok8/openwrt-daede` 一个来源。** 早先镜像过的
`QiuSimons/luci-app-daed`（旧 `dae/` 路径）已移除：固件从 my-ImmortalWrt commit `8955399`
起就不再使用它，而两份来源并存会在同一个 package 树里重复定义 `dae` 和 `daed`。

### 仅镜像保留

以下包跟随上游同步，但不会编译进 my-ImmortalWrt；只有固件 `.config` 显式选择时才参与构建。

| 包 | 路径 | 上游 |
|----|------|------|
| `golang` | `golang` | `sbwml/packages_lang_golang`（`26.x` 分支） |
| `luci-app-adguardhome` | `luci-app-adguardhome` | `rufengsuixing/luci-app-adguardhome` |
| `luci-app-mosdns` | `luci-app-mosdns` | `sbwml/luci-app-mosdns` |
| `luci-app-smartdns` | `luci-app-smartdns` | `pymumu/luci-app-smartdns` |
| `luci-app-vlmcsd` | `luci-app-vlmcsd` | `mchome/luci-app-vlmcsd` |
| `openclash` | `openclash` | `vernesong/OpenClash` |
| `openwrt-passwall` | `openwrt-passwall` | `Openwrt-Passwall/openwrt-passwall` |
| `openwrt-passwall2` | `openwrt-passwall2` | `Openwrt-Passwall/openwrt-passwall2` |

`golang` 保留但当前固件不用：my-ImmortalWrt 已改用 ImmortalWrt 官方
`packages/lang/golang`（master / openwrt-25.12 均为 Go 1.26.x），不再覆盖官方 Go 包。

### 本地维护

`adguardhome-dual/` 由本仓库维护，不在 `sources.json` 中，不会被同步覆盖。

当前固件同样不编译它——已改用官方 `adguardhome` 包提供二进制，再用 overlay 提供
`adh-direct` / `adh-proxy` 双实例。**注意该包默认把 Web UI 开在
`192.168.50.1:50080/50081`，而固件当前方案在未设置登录认证前只监听 loopback。**
若要重新启用这个包，先确认这一差异是你想要的。

## 在 OpenWrt 中使用

> **不要把整个仓库克隆进 `package/`。** 本仓库刻意保存了并非同一套方案的包：
> `golang/` 会与 `feeds/packages/lang/golang` 产生重复的包定义；`adguardhome-dual`
> 会与基于官方 `adguardhome` 的 overlay 双实例方案冲突；`openclash` / `passwall` 等
> 也会被包扫描一并纳入。请只取需要的子目录。

克隆到源码树之外，再把需要的子目录复制进 `package/`：

```sh
git clone --depth 1 https://github.com/hellomrli/my-openwrt-packages.git /tmp/my-openwrt-packages
cp -r /tmp/my-openwrt-packages/openwrt-daede     package/dae
cp -r /tmp/my-openwrt-packages/luci-app-lucky    package/lucky
cp -r /tmp/my-openwrt-packages/luci-app-watchdog package/watchdog
```

然后在 `.config` 中显式选择需要的包：

```text
CONFIG_PACKAGE_dae=y
CONFIG_PACKAGE_daed=y
CONFIG_PACKAGE_luci-app-daede=y
CONFIG_PACKAGE_luci-app-lucky=y
CONFIG_PACKAGE_luci-app-watchdog=y
```

`daed` 需要内核启用 eBPF / BTF / XDP。my-ImmortalWrt 的配置保留 kernel BTF 并显式选择
`DAED_USE_KERNEL_BTF`。

同名包不要同时存在多份定义，否则 `make defconfig` 阶段的包扫描会产生冲突。

## my-ImmortalWrt 如何取包

固件用
[`.github/scripts/fetch-packages.py`](https://github.com/hellomrli/my-ImmortalWrt/blob/main/.github/scripts/fetch-packages.py)
自动完成上述步骤：

- **镜像优先、上游兜底。** 镜像不可达或尚未收录某个包时，回退直连上游并打印 `::warning::`，
  构建不中断——所以本仓库新增包和固件改动可以分开上线。
- 落地后校验每个包的必需 `Makefile`。
- 把实际来源和 commit 写进 `package-provenance.txt`，随固件 Release 发布，
  这样任何一个镜像固件都能追溯到确切的包版本。

上游改动打断固件构建时，把固件 `.github/packages.json` 的 `mirror.ref` 从 `main` 改成本仓库
某个已知可用的 commit SHA，即可一次性冻结全部第三方包，不必依赖上游修复。

## 同步机制

同步源由 [`sources.json`](sources.json) 管理，脚本是
[`scripts/sync-packages.py`](scripts/sync-packages.py)：

```sh
python3 scripts/sync-packages.py
```

流程：

1. 读取 `sources.json`。
2. 浅克隆每个上游仓库。
3. 移除上游 `.git` 元数据。
4. 覆盖本仓库对应目录。
5. 重新生成 [`SYNCED_SOURCES.md`](SYNCED_SOURCES.md)，记录分支和 commit。

GitHub Actions 每 6 小时自动同步一次，也可手动运行 `Sync OpenWrt Packages`。

脚本是 fail-closed 的：先克隆成功才替换目录，所以上游临时不可达不会把镜像里的好副本删掉。
代价是任何一个上游失败都会中止整轮同步——上游确实消失时，请把该条目从 `sources.json`
移除，让其余包恢复更新。

## 维护规则

新增同步包：

1. 在 `sources.json` 增加 `name` / `path` / `repo`（可选 `branch`）和 `description`。
2. 运行 `python3 scripts/sync-packages.py`。
3. 检查变更目录和 `SYNCED_SOURCES.md`。
4. 需要编译进固件时，再在 my-ImmortalWrt 的 `.github/packages.json` 和 `.config` 中启用。

新增本地维护包时，**不要**把路径写进 `sources.json`，否则下一次同步会覆盖本地改动。

移除包时，同时删掉 `sources.json` 条目和对应目录——同步脚本不会清理已不在清单中的目录。

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
python3 -m json.tool sources.json >/dev/null
```
