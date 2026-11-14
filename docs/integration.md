# 抽取包并固定构建来源

[返回文档入口](index.md)

## 按目录集成

先在源码树外克隆镜像，再复制目标目录。Honk 示例：

```sh
git clone --depth 1 https://github.com/hellomrli/my-openwrt-packages.git /tmp/my-openwrt-packages
cp -r /tmp/my-openwrt-packages/openwrt-honk package/honk
make menuconfig
make defconfig
```

Honk stage 文件已将资源 URL 指向本仓库的 Release，下载时校验锁定摘要。保留这些 URL，避免重新依赖上游。

如采用 dae/daed，可复制 `openwrt-daede` 并在配置中选择 `dae`、`daed`、`luci-app-daede`。需要匹配内核的 eBPF/BTF/XDP；不能将新内核模块强装到旧内核。

整仓复制会让未选择的包也进入扫描，Go 包和相互替代的服务方案易产生重复定义。复制前清理自己的同名定义，并确认依赖来自哪一份 feeds。

## 集成到 my-ImmortalWrt

固件以当前目标的包清单为准：master 用 `.github/packages-honk.json`，IPTV 用 `.github/packages-iptv.json`。旧 `.github/packages.json` 等清单不应被当作当前目标入口。

[fetch-packages.py](https://github.com/hellomrli/my-ImmortalWrt/blob/main/.github/scripts/fetch-packages.py) 按目录抽取、校验必需文件，并记录 `package-provenance.txt`。普通包在 auto 模式下可以回退上游；Honk 标记 mirror-only，不能回退。

需要复现某个已知可用版本时，将对应清单的 `mirror.ref` 固定到 commit SHA，并设置 `PKG_SOURCE=mirror`。Lucky 核心会独立解析官方发布，需另行设置 `LUCKY_VERSION`；仅固定包镜像不保证冻结所有外部二进制。

最终版本以该次固件 Release 的来源清单为准。包同步本身不触发固件重编。
