# IPTV 回放与时移（LuCI）

广西移动 IPTV 历史 TS → HLS 的轻量服务。**不录制、不代理视频**；播放器通过 302
直接请求现有网关的视频分片，路由器只生成播放列表并探测历史分片。

当前源码版本为 **0.1.2-r1**，保留小幅序号漂移搜索、相邻分片纠偏复用、已观测时间线断点、
HTTP(S)/DNS 总截止时间及 HLS 起播参数；新增有上限的请求诊断与可配置 JSON 日志，修复响应写入
失败时重复发送错误响应及占用上游名额的问题。仅完成本机离线验证，尚未在路由器/电视部署验收。
现有 Release 的 0.1.0-r1 不包含这些修复；请先导出、构建新版本，**不要在观看中安装升级**。

## 功能与边界

- LuCI：**服务 → IPTV 回放与时移**，配置启停、网关、LAN 监听、端口、订阅地址和时间格式。
- procd 开机启动、进程守护、配置 / LAN 变化重载；以 `nobody:nogroup` 运行。
- 默认自动绑定 LAN IPv4 的 **8088**，不创建防火墙规则，不替换现有网关 / 扫描服务。
- 三种订阅动态生成，保留原频道信息和 EPG；不把生成结果反复写入闪存。
- `/status` 提供耗时、上游探测、缓存命中、纠偏统计及最近各 20 条失败/慢请求；LuCI 可选择日志模式。
- 单次回放最多 4 小时；时移窗口最多 4 小时；北京时间与 Unix 秒均可使用。
- 内置 124 个频道、79 个回放白名单；天数沿用原型观测，不保证全部频道 / 天数可用。
- **不是通用录像服务器。** 要求广西移动源及现有网关仍提供未加密、带时间戳的 10 秒 TS。

**无认证，仅限可信局域网。不要配置公网端口转发。** 该包不提供直播网关、运营商网络接入
或 EPG 服务。电视 / 播放器须能访问服务及 302 返回的视频地址，回放 UI 还依赖播放器
支持 M3U `catchup-source` 和对应 EPG；服务端探测通过不等于所有播放器均兼容。

## 安装 APK

适用于 APK 版 OpenWrt / ImmortalWrt，Python **3.10+**。本包纯脚本，APK 架构为 `noarch`。
先按本文导出、构建 `luci-app-iptv-catchup-0.1.2-r1.apk`，核对 `SHA256SUMS`，再上传路由器。
以下是安装命令示例，**不表示此版本已发布 APK**：

```sh
apk update
apk add --allow-untrusted /tmp/luci-app-iptv-catchup-0.1.2-r1.apk
```

本地构建包未签名，仅安装本仓库经校验的文件；不要关闭全局签名验证。`python3` 依赖会自动
安装，完整标准库占用大于服务本身。需要有足够 overlay 空间，不建议小闪存设备使用。

安装后默认停用。LuCI 中填好**路由器与播放器都能访问**的直播网关（例如
`http://192.168.50.250:8080`），启用后“保存并应用”。若页面没出现，请重新登录 LuCI / 刷新浏览器。

默认网关 `127.0.0.1:8080` 仅为待配置占位，不能直接作为电视端直播地址，务必改成 LAN 地址。

## 订阅与接口

以 LAN 地址 `192.168.50.250`、端口 `8088` 为例：

| 路径 | 用途 |
| --- | --- |
| `http://192.168.50.250:8088/IPTV-catchup.m3u` | 推荐，酷9 / 回放订阅，直播走所配置网关 |
| `http://192.168.50.250:8088/IPTV.m3u` | 仅直播，无回放属性 |
| `http://192.168.50.250:8088/gx.m3u` | 运营商直连，不走直播网关，需 IPTV 网络 |
| `/catchup/{cid}/{start}/{end}/index.m3u8` | 节目回放；支持 Unix 秒或北京时间 `yyyyMMddHHmmss` |
| `/timeshift/{cid}/{start}/index.m3u8` | 连续时移 HLS；滑动最多 4 小时，不含 ENDLIST |
| `/status` | 进程健康、缓存、请求诊断及最近失败，不代表上游可播放 |

默认 `ku9` 使用 `${(b)yyyyMMddHHmmss}` / `${(e)yyyyMMddHHmmss}`；通用 `unix`
使用 `{utc}` / `{utcend}`。切换格式后在播放器中刷新订阅。酷9 的“节目回放”使用 catchup
接口；`timeshift` 接口需要播放器显式调用，并不承诺所有播放器都有任意时移按钮。
已结束节目输出 VOD，进行中节目持续增长；限制到直播边缘之前 20 秒，避免尚未生成的分片。
超出白名单返回 404，超出保留期返回 416，上游异常返回 502 / 504，并发满返回 503。
纠偏提示仅用于相邻媒体时间，并在使用前探测验证；不会跨频道或跨远距离拖动盲目套用。
时间线断点只保留请求时观测到的记录，不能凭一个直播锚点重建任意历史节目中的 PTS 重置。
已观测到的 10 秒时间戳相位改变会明确返回 502，不生成错位播放列表。

## 配置、日志与卸载

- UCI：`/etc/config/iptv-catchup`，修改后执行 `/etc/init.d/iptv-catchup restart`。
- 频道：`/etc/iptv-catchup/IPTV.m3u`。可手动更新，与扫描插件的频道库独立，不会自动同步。
- 白名单：`/etc/iptv-catchup/catchup.json` 中 `days_by_channel`，按**精确频道名称**匹配，1–7 天。
  删除键即禁用该频道回放。`base_url` / `format` 由 UCI 覆盖，但 JSON 仍需保持合法。
- 日志：`logread | grep -E 'iptv-catchup|IPTV catchup|python3'`；状态：`ubus call service list '{"name":"iptv-catchup"}'`。
- 检查：`wget -qO- http://192.168.50.250:8088/status`。
- 诊断：LuCI 的“诊断日志”对应 UCI `access_log`，默认 `errors` 仅记录失败和耗时至少 2 秒的请求；
  `all` 用于临时逐请求排障，`off` 关闭日志但保留内存统计。日志用 `logread | grep 'iptv_request'` 查看。
- 卸载：`apk del luci-app-iptv-catchup`；自定义配置可能保留，手动删除前请备份。

三个配置 / 数据文件是 conffiles，APK 默认保护 `/etc` 中已修改文件；升级后注意检查 `.apk-new`。
同时提供 OpenWrt 的 `.conffiles` 元数据用于 sysupgrade 备份。**仍应自行备份配置及频道表**。
原有 8080 网关、8081 扫描器均独立运行，本包不管理或停止它们。

诊断信息不落盘，重启后清空；响应的 `X-Request-ID` 可关联状态里的记录及 JSON 日志。
日志不含完整 URL、查询参数、客户端 IP、请求头或会话令牌，但仍含频道与节目时间，分享时请注意隐私。
这些耗时和探测指标仅描述回放服务的解析过程，**不测量电视下载整个 TS 或解码的情况**。
修改日志模式同样会重启服务，应先保存需要的状态信息，且不要在观看时操作。

## 源码编译（APK / IPK）

my-IPTV 仓库中的此目录是模板，不含自动注入的运行脚本和频道数据。先从 my-IPTV 根目录导出到
一个不存在的新目录：

```sh
python3 scripts/export_openwrt.py /tmp/luci-app-iptv-catchup-0.1.2
python3 /tmp/luci-app-iptv-catchup-0.1.2/tests/check.py
```

再将**导出后的目录**复制到固件源码 / SDK 的 `package/luci-app-iptv-catchup`：

```sh
./scripts/feeds update -a
./scripts/feeds install -a
make menuconfig  # LuCI -> Applications -> luci-app-iptv-catchup
make package/luci-app-iptv-catchup/compile V=s
```

构建系统决定输出 APK 或 IPK。旧版 opkg 固件请用匹配的 SDK 编译 IPK，不能安装此 APK。

本包无本机编译代码，也可在导出目录中用 upstream apk-tools **3.0.5**（启用 `mkpkg`）打 APK：

```sh
python3 tests/check.py
fakeroot python3 tools/build_apk.py --apk /path/to/apk-tools/build/src/apk --output dist
```

此快捷构建保留 root 权限、依赖、启动 / 卸载脚本、conffiles 和升级脚本；不要求 OpenWrt
交叉工具链。本仓库检查工作流验证 Python 3.10 / 3.14 及独立导出包，不自动发布或部署 APK。
`SOURCE.json` 记录精确源码 SHA256；修改打包文件后，
应从 my-IPTV 的 `scripts/export_openwrt.py` 重新导出，不要忽略校验错误。

上游思路来自 `hellomrli/my-IPTV` 的 `test` 分支；当前快照补齐时区、半小时目录、分片相位、
会话刷新、短探测、边界验证、并发 / 超时和离线回归测试。服务端不转码，不落盘缓存视频。

## 0.1.0-r1 验证记录

2026-10-24，在 x86_64 ImmortalWrt SNAPSHOT `r0-8735c68` / APK 3.0.5 上实际安装，
由 procd 以 `nobody:nogroup` 启动；原有 8080 直播网关和 8081 扫描器保持运行。

- 上游完整离线测试 58 项、包内离线测试 39 项通过；两次构建 APK 字节一致。
- 三个订阅均返回 124 个频道，回放订阅有 79 个白名单频道。
- CCTV1 的 Unix 时间及北京时间短回放通过；抽查首尾历史 TS 均返回 206。
- 时移与进行中节目列表在间隔 12 秒后继续增长，不带 `ENDLIST`。
- `ffprobe` 读取真实回放 HLS，识别 HEVC 1920×1080 和 AAC，退出码 0。
- 真实浏览器访问 LuCI，表单、进程状态、订阅链接正常，无 JavaScript 异常。
- UCI 配置重载时，端口和订阅地址同步切换，测试后恢复 8088。

这是单频道短时服务端验证，**未覆盖全部频道、全部历史天数或电视端播放器交互**。
电视端请刷新 / 导入回放订阅，再选择 EPG 中已结束的节目测试。
