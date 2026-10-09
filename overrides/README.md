# overrides

同步之后覆盖回仓库的文件。目录结构与仓库根目录一致，例如
`overrides/luci-app-lucky/lucky/Makefile` 会覆盖 `luci-app-lucky/lucky/Makefile`。

`scripts/sync-packages.py` 先整仓同步 `sources.json` 里的上游，再按本目录逐文件覆盖，
覆盖结果会写进 [`SYNCED_SOURCES.md`](../SYNCED_SOURCES.md)。覆盖路径必须落在本次同步
出来的某个包目录内，写错路径会直接报错退出，不会悄悄多出一个上游没有的目录。

## 什么时候需要它

上游仓库还在同步、但其中某个包必须长期偏离上游时。放在这里的文件必须写明为什么不能
跟随上游，否则下一个人会以为是忘了同步。

## 当前内容

| 文件 | 原因 |
|------|------|
| `luci-app-lucky/lucky/Makefile` | Lucky 从 3.x 起只在官方文件服务 <https://release.66666.host/> 发布，GitHub 的 `gdy666/lucky` 停在 v2.27.2 且不再有 3.x 资产；上游 `gdy666/luci-app-lucky` 里那份 Makefile 仍指向 GitHub release，同步回来会把核心包退回 2.27.2。 |

`luci-app-lucky/luci-app-lucky/`（LuCI 界面）与 `lucky/files/`（init 脚本、`lucky-call`
辅助程序）继续跟随上游同步：上游界面包已经是 3.0.0，与官方站点发布的
`luci-app-lucky-3.0.0-r1.apk` 同版本，`lucky-call` 用到的 `-info` / `-baseConfInfo` /
`-setconf` 接口在 3.1.4 二进制上实测可用。

版本号、下载地址与逐架构 sha256 不靠手工维护：固件仓库 `hellomrli/my-ImmortalWrt` 的
`.github/scripts/pin-lucky-source.py` 会在每次构建前按官方 `checksums.txt` 重写它们，
这里保存的是最近一次已知可用的状态，供镜像单独使用时兜底。
