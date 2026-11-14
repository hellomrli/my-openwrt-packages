# 覆盖自动同步的文件

本目录保存必须偏离上游的文件，使下一轮同步仍能保留本地包定义。

[维护指南](../docs/maintenance.md) · [同步记录](../SYNCED_SOURCES.md)

## 放置覆盖文件

目录结构与仓库根一致。例如 `overrides/luci-app-lucky/lucky/Makefile` 覆盖 `luci-app-lucky/lucky/Makefile`。同步后逐文件覆盖，结果记入来源表。

在仓库根目录核对现有覆盖文件：

```bash
sed -n '1,120p' overrides/luci-app-lucky/lucky/Makefile
```

覆盖路径须属于本轮同步包目录，错误路径会报错。新增覆盖时说明原因；不要直接修改生成副本，也不要在这里加入运行账户、订阅或设备数据库。

## Lucky 核心为何固定

Lucky 3.x 的核心二进制和 APK 从 [官方文件服务](https://release.66666.host/) 发布，GitHub 的 `gdy666/lucky` Release 停在 v2.27.2。沿用旧上游 Makefile 会回退核心版本，因此覆盖核心 Makefile 中的版本、下载地址和逐架构摘要。

LuCI、init 和 `lucky-call` 仍跟随上游。此前在 3.1.4 上验证过 `-info`、`-baseConfInfo` 和 `-setconf`，这是一份历史验证，后续版本需重新核对。

my-ImmortalWrt 的 `.github/scripts/pin-lucky-source.py` 在构建前按官方 `checksums.txt` 刷新核心版本与摘要；这里提供最近一次已知可用的固定状态。
