# 包目录与来源

[返回文档入口](index.md)

## 当前 master/Honk 固件取用

| 组件 | 镜像路径 | 来源与条件 |
|---|---|---|
| Honk / LuCI / Doona | `openwrt-honk` | `kenzok8/openwrt-honk`，正式发布及摘要绑定；固件只用镜像 |
| Lucky / LuCI | `luci-app-lucky` | `gdy666/luci-app-lucky`；核心版本和逐架构 SHA256 由 overrides 固定 |
| Watchdog / LuCI | `luci-app-watchdog` | `sirpdboy/luci-app-watchdog` |

当前 master 固件已由 dae/daed 改用 Honk。`openwrt-daede` 仍保存 `kenzok8/openwrt-daede` 的 dae、daed 和 LuCI，适用于自行选择此方案的构建；需要 eBPF、BTF 和 XDP 内核支持。旧 `QiuSimons/luci-app-daed` 路径已移除，避免重复包定义。

## 其他同步目录

以下目录保留上游副本。是否参与某个固件目标由该目标的包清单和 `.config` 决定。

| 包 | 路径 | 上游 |
|----|------|------|
| `golang` | `golang` | `sbwml/packages_lang_golang`（`27.x` 分支，最新 Go 轨道） |
| `luci-app-adguardhome` | `luci-app-adguardhome` | `rufengsuixing/luci-app-adguardhome` |
| `luci-app-mosdns` | `luci-app-mosdns` | `sbwml/luci-app-mosdns` |
| `luci-app-smartdns` | `luci-app-smartdns` | `pymumu/luci-app-smartdns` |
| `luci-app-vlmcsd` | `luci-app-vlmcsd` | `mchome/luci-app-vlmcsd` |
| `openclash` | `openclash` | `vernesong/OpenClash` |
| `openwrt-passwall` | `openwrt-passwall` | `Openwrt-Passwall/openwrt-passwall` |
| `openwrt-passwall2` | `openwrt-passwall2` | `Openwrt-Passwall/openwrt-passwall2` |

`golang` 保留但当前固件不用：my-ImmortalWrt 已改用 ImmortalWrt 官方
`packages/lang/golang`（master 默认 Go 1.27，openwrt-25.12 为 Go 1.26），不再覆盖官方 Go 包。

## 本地维护或导出组件

| 目录 | 用途 | 注意事项 |
|---|---|---|
| `luci-app-iptv-catchup` | 广西移动回放/时移 LuCI 与 Python 服务 | 已导出、带 SOURCE.json 摘要的快照；实际发布见安装指南 |
| `gxmobile-scan` / `luci-app-gxmobile` | x86_64 IPTV 扫描后端与 LuCI | 配套安装，需自己的运营商网关 |
| `adguardhome-dual` | 独立双 ADH 包 | 当前 master 不使用；默认无认证 LAN Web 与当前 overlay 方案不同 |

本地目录不在 `sources.json` 中，自动同步不覆盖。版本以对应 Makefile、源码清单和 Release 为准，不把历史验证数量视为当前服务可用性证明。
