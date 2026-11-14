# my-openwrt-packages

my-openwrt-packages 为 OpenWrt / ImmortalWrt 构建者保存第三方包源码和部分发布资源，减少固件构建对上游仓库可达性的依赖。

[![Sync OpenWrt Packages](https://github.com/hellomrli/my-openwrt-packages/actions/workflows/sync-packages.yml/badge.svg)](https://github.com/hellomrli/my-openwrt-packages/actions/workflows/sync-packages.yml)

## 将一个包加入固件源码

在已准备好 `packages` 和 `luci` feeds 的 OpenWrt 源码根目录执行，仅复制需要的子目录：

```sh
git clone --depth 1 https://github.com/hellomrli/my-openwrt-packages.git /tmp/my-openwrt-packages
cp -r /tmp/my-openwrt-packages/luci-app-lucky package/lucky
make menuconfig
```

选择对应包后运行 `make defconfig`，再按自己的目标架构编译。目标路径应为空，同名包只保留一份定义。不要将整仓放入 `package/`：镜像中的 Go、代理和双 ADH 包可能与官方 feeds 或自己的方案冲突。

已生成的 IPTV 回放 APK 可从 [安装指南](docs/catchup-install.md) 获取；普通镜像目录不是通用的预编译软件包集合。

## 选择包来源

- 当前 [my-ImmortalWrt](https://github.com/hellomrli/my-ImmortalWrt) 的 master/Honk 目标使用 Honk、Lucky、Watchdog 等指定目录。
- dae/daed、OpenClash、Passwall、MosDNS 等目录继续保留，是否使用由自己的构建配置决定。
- Honk 按上游正式 Release 同步，wrapper、核心、Doona 和摘要相互绑定，资源重新镜像到本仓库 Release。
- `gxmobile-scan`、`luci-app-gxmobile`、`luci-app-iptv-catchup` 与 `adguardhome-dual` 是本地维护或导出的组件，不加入自动同步清单。

自动同步每 6 小时运行，更新源码不会直接触发固件重编。实际来源、commit 和覆盖文件见 [SYNCED_SOURCES.md](SYNCED_SOURCES.md)。

## 查阅构建和维护文档

- [文档入口](docs/index.md)
- [包目录、来源和选择条件](docs/packages.md)
- [抽取包、固定版本与固件集成](docs/integration.md)
- [同步、覆盖和故障处理](docs/maintenance.md)
- [IPTV 回放 APK 安装](docs/catchup-install.md)
- [IPTV 扫描界面](luci-app-gxmobile/README.md) · [扫描后端](gxmobile-scan/README.md)

## 来源、反馈与许可

第三方代码、README 和许可证随镜像保留，使用各自上游授权。本仓库没有统一的根目录许可证，不能把所有包视为同一种授权。反馈同步或本地组件问题请提交 [Issue](https://github.com/hellomrli/my-openwrt-packages/issues)，注明目录、来源 commit 和构建目标。

不要提交设备订阅、账户、密钥、运行数据库或真实配置备份。
