# luci-app-gxmobile

广西移动 IPTV 扫描管理界面，配套后端为相邻的 [`gxmobile-scan`](../gxmobile-scan/)。
适用于 x86_64 OpenWrt / ImmortalWrt，使用现代 LuCI JavaScript 与 rpcd ucode。版本以配套 Makefile 为准。

[仓库文档](index.md) · [后端说明](../gxmobile-scan/README.md)

## 扫描并导出频道

- 手动快速扫描、完整 CID 范围扫描和扫描状态查看。
- 每日或每周定时扫描，统一管理 cron，避免重复任务。
- 频道重命名、删除、筛选，下载 M3U 列表和扫描报告。
- 后端仅监听 `127.0.0.1:8081`，管理操作通过 LuCI 登录权限访问。

扫描需要可访问的广西移动 IPTV HTTP 网关；请在界面中设置适合自己网络的地址和 CID 范围。
此组件不提供运营商接入或组播代理服务。扫描数据保存在 `/etc/gxmobile`，配置为
`/etc/config/gxmobile`。发布的播放列表位于 `/www/gxmobile`，可由局域网 Web 服务访问。

## 从源码构建并安装

在已经准备好官方 `packages` 和 `luci` feeds 的 OpenWrt / ImmortalWrt 源码根目录执行：

```sh
git clone --depth 1 https://github.com/hellomrli/my-openwrt-packages.git /tmp/my-openwrt-packages
cp -r /tmp/my-openwrt-packages/gxmobile-scan package/gxmobile-scan
cp -r /tmp/my-openwrt-packages/luci-app-gxmobile package/luci-app-gxmobile
make menuconfig
```

目标选 x86/64，在 LuCI → Applications 中选择 `luci-app-gxmobile`，自动选择扫描后端。
Go 使用官方 `feeds/packages/lang/golang`。调度器需要 BusyBox 的 `flock` applet
（`CONFIG_BUSYBOX_CONFIG_FLOCK=y`）或 util-linux 的 `flock` 包。

```sh
make defconfig
make package/gxmobile-scan/compile V=s
make package/luci-app-gxmobile/compile V=s
```

首次编译应先准备工具链，或直接完整编译固件。将生成的两个包一并安装到对应版本固件，
重新登录 LuCI 后从 **服务 → IPTV 扫描** 进入。

## 来源与维护

两包由自动任务从 [my-ImmortalWrt](https://github.com/hellomrli/my-ImmortalWrt) 的同一个提交导出；
请在固件仓库的 `packages/` 修改源码，不单独编辑镜像副本。实际提交见 [来源表](../SYNCED_SOURCES.md)。
功能限制与升级说明见 [IPTV 文档](https://github.com/hellomrli/my-ImmortalWrt/blob/main/docs/iptv.md)。
