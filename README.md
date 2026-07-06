# my-openwrt-packages

Personal OpenWrt package mirror used by [`hellomrli/my-ImmortalWrt`](https://github.com/hellomrli/my-ImmortalWrt).

This repository keeps a small, curated set of packages instead of importing a large third-party feed.

Extra packages marked `not selected yet` are mirrored only. They are not compiled into `my-ImmortalWrt` until the firmware `.config` explicitly selects them.

## Packages

| Package | Upstream | Local path | Used by |
|---------|----------|------------|---------|
| luci-app-lucky | https://github.com/gdy666/luci-app-lucky | `luci-app-lucky` | all images |
| luci-app-watchdog | https://github.com/sirpdboy/luci-app-watchdog | `luci-app-watchdog` | all images |
| OpenClash | https://github.com/vernesong/OpenClash | `openclash` | `immortalwrt/master`, `immortalwrt/openwrt-25.12` |
| luci-app-daed / daed | https://github.com/QiuSimons/luci-app-daed | `dae` | `immortalwrt-daed/master` |
| luci-app-adguardhome | https://github.com/rufengsuixing/luci-app-adguardhome | `luci-app-adguardhome` | not selected yet |
| luci-app-mosdns | https://github.com/sbwml/luci-app-mosdns | `luci-app-mosdns` | not selected yet |
| openwrt-passwall | https://github.com/Openwrt-Passwall/openwrt-passwall | `openwrt-passwall` | not selected yet |
| openwrt-passwall2 | https://github.com/Openwrt-Passwall/openwrt-passwall2 | `openwrt-passwall2` | not selected yet |
| luci-app-vlmcsd | https://github.com/mchome/luci-app-vlmcsd | `luci-app-vlmcsd` | not selected yet |
| luci-app-smartdns | https://github.com/pymumu/luci-app-smartdns | `luci-app-smartdns` | not selected yet |

## Sync

Packages are synced by GitHub Actions every 6 hours and can also be synced manually:

```bash
python3 scripts/sync-packages.py
```

The sync script records upstream commit information in [`SYNCED_SOURCES.md`](SYNCED_SOURCES.md).

## Use in OpenWrt build

Clone this repository into the OpenWrt source tree under `package/`:

```bash
git clone --depth 1 https://github.com/hellomrli/my-openwrt-packages.git package/my-openwrt-packages
```

Then select packages in `.config` as usual, for example:

```text
CONFIG_PACKAGE_luci-app-openclash=y
CONFIG_PACKAGE_luci-app-lucky=y
CONFIG_PACKAGE_luci-app-watchdog=y
```

or:

```text
CONFIG_PACKAGE_daed=y
CONFIG_PACKAGE_luci-app-daed=y
CONFIG_PACKAGE_luci-app-lucky=y
CONFIG_PACKAGE_luci-app-watchdog=y
```

## Firmware build policy

Package sync only updates this mirror. It does **not** trigger `hellomrli/my-ImmortalWrt` firmware builds by itself.

Firmware builds clone this repository at build time, so manual builds or ImmortalWrt-upstream-triggered builds automatically use the latest mirrored packages available at that moment.
