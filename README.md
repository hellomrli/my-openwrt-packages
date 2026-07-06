# my-openwrt-packages

Personal OpenWrt package mirror used by [`hellomrli/my-ImmortalWrt`](https://github.com/hellomrli/my-ImmortalWrt).

This repository keeps a small, curated set of packages instead of importing a large third-party feed.

## Packages

| Package | Upstream | Local path | Used by |
|---------|----------|------------|---------|
| luci-app-lucky | https://github.com/gdy666/luci-app-lucky | `luci-app-lucky` | all images |
| luci-app-watchdog | https://github.com/sirpdboy/luci-app-watchdog | `luci-app-watchdog` | all images |
| OpenClash | https://github.com/vernesong/OpenClash | `openclash` | `immortalwrt/master`, `immortalwrt/openwrt-25.12` |
| luci-app-daed / daed | https://github.com/QiuSimons/luci-app-daed | `dae` | `immortalwrt-daed/master` |

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

## Optional firmware build trigger

To trigger `hellomrli/my-ImmortalWrt` immediately after package updates, add a repository secret named `FIRMWARE_REPO_TOKEN`.

The token needs permission to run workflows in `hellomrli/my-ImmortalWrt`.
If the secret is not configured, packages will still sync automatically; the firmware repository can monitor this repository from its own update checker.
