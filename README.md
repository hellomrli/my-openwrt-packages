# my-openwrt-packages

[![Sync OpenWrt Packages](https://github.com/hellomrli/my-openwrt-packages/actions/workflows/sync-packages.yml/badge.svg)](https://github.com/hellomrli/my-openwrt-packages/actions/workflows/sync-packages.yml)

个人 OpenWrt / ImmortalWrt 第三方包镜像仓库，供 [`hellomrli/my-ImmortalWrt`](https://github.com/hellomrli/my-ImmortalWrt) 云编译时使用。

这个仓库只同步一组小而明确的包，避免在固件构建中引入大型第三方 feed。包源码会定时跟随上游更新，但**包同步本身不会触发固件编译**。

## 包清单

| Package | Upstream | Local path | Used by |
|---------|----------|------------|---------|
| luci-app-lucky | https://github.com/gdy666/luci-app-lucky | `luci-app-lucky` | all images |
| luci-app-watchdog | https://github.com/sirpdboy/luci-app-watchdog | `luci-app-watchdog` | all images |
| OpenClash | https://github.com/vernesong/OpenClash | `openclash` | `immortalwrt/master`, `immortalwrt/openwrt-25.12` |
| luci-app-daed / daed | https://github.com/QiuSimons/luci-app-daed | `dae` | `immortalwrt-daed/master` |
| luci-app-adguardhome | https://github.com/rufengsuixing/luci-app-adguardhome | `luci-app-adguardhome` | mirrored only |
| luci-app-mosdns | https://github.com/sbwml/luci-app-mosdns | `luci-app-mosdns` | mirrored only |
| openwrt-passwall | https://github.com/Openwrt-Passwall/openwrt-passwall | `openwrt-passwall` | mirrored only |
| openwrt-passwall2 | https://github.com/Openwrt-Passwall/openwrt-passwall2 | `openwrt-passwall2` | mirrored only |
| luci-app-vlmcsd | https://github.com/mchome/luci-app-vlmcsd | `luci-app-vlmcsd` | mirrored only |
| luci-app-smartdns | https://github.com/pymumu/luci-app-smartdns | `luci-app-smartdns` | mirrored only |

`mirrored only` 表示只保留源码镜像，除非固件 `.config` 显式选择，否则不会编译进 `my-ImmortalWrt`。

## 同步机制

同步源由 [`sources.json`](sources.json) 管理：

```bash
python3 scripts/sync-packages.py
```

脚本会：

1. 读取 `sources.json`。
2. 使用浅克隆拉取上游仓库。
3. 删除上游 `.git` 和工作流等不需要的内容。
4. 覆盖本仓库对应目录。
5. 更新 [`SYNCED_SOURCES.md`](SYNCED_SOURCES.md)，记录本次同步的分支和 commit。

GitHub Actions 每 6 小时自动同步一次，也支持手动运行 `Sync OpenWrt Packages`。

## 在 OpenWrt / ImmortalWrt 中使用

在 OpenWrt 源码树中克隆到 `package/` 下：

```bash
git clone --depth 1 https://github.com/hellomrli/my-openwrt-packages.git package/my-openwrt-packages
```

OpenClash 镜像示例：

```text
CONFIG_PACKAGE_luci-app-openclash=y
CONFIG_PACKAGE_luci-app-lucky=y
CONFIG_PACKAGE_luci-app-watchdog=y
```

Daed 镜像示例：

```text
CONFIG_PACKAGE_daed=y
CONFIG_PACKAGE_luci-app-daed=y
CONFIG_PACKAGE_luci-app-lucky=y
CONFIG_PACKAGE_luci-app-watchdog=y
```

## 与 my-ImmortalWrt 的关系

- `my-ImmortalWrt` 在固件构建开始时克隆本仓库，因此会使用当时 `main` 上的最新镜像包。
- 本仓库更新不会主动触发固件构建，避免上游包高频更新导致固件频繁重编。
- 若需要完全可复现构建，建议在固件 Release 中记录本仓库 commit，或在构建脚本中临时 pin 到指定提交。

## 维护建议

新增包时：

1. 在 `sources.json` 增加上游仓库、目标路径和描述。
2. 本地运行 `python3 scripts/sync-packages.py`。
3. 检查 `SYNCED_SOURCES.md` 和目录结构。
4. 如果固件要编译该包，再到 `my-ImmortalWrt` 的 `.config` 中显式选择。

## 安全与脱敏

- 本仓库只保存公开上游包源码，不应提交路由器配置、订阅地址、Cookie、API Key 或私有证书。
- 同步脚本不会读取本地 OpenWrt 配置文件，也不会写入固件仓库。
- 如果上游包自带示例密钥，请确认只是公开示例后再保留。

## 本地检查

```bash
python3 -m py_compile scripts/sync-packages.py
python3 -m json.tool sources.json >/tmp/sources.json.check
```
## Local packages

- `adguardhome-dual`: dual AdGuardHome runtime for dnsmasq + daed DNS routing. It installs ADH Core v0.107.77 from the official AdGuardHome release and provides `adh-direct` / `adh-proxy` procd services.
- `golang`: OpenWrt official `packages/lang/golang` packaging with Go 1.26.5 pinned to the official Go source release (`go.dev/dl` / `dl.google.com/go`).

