# 安装 IPTV 回放与时移组件

[返回文档入口](index.md)

## 下载并安装 APK

[iptv-catchup-v0.1.2-r1](https://github.com/hellomrli/my-openwrt-packages/releases/tag/iptv-catchup-v0.1.2-r1) 已提供 `luci-app-iptv-catchup-0.1.2-r1.apk` 和 `SHA256SUMS`。下载后在电脑校验，上传 `/tmp`，在 APK 版 OpenWrt/ImmortalWrt 安装：

```sh
sha256sum -c SHA256SUMS
# 以下在路由器执行
apk add --allow-untrusted /tmp/luci-app-iptv-catchup-0.1.2-r1.apk
```

需要 Python 3.10+ 和足够的 overlay 空间。包未签名，`--allow-untrusted` 仅用于这份已核对的本地包，不要关闭全局验证。opkg 固件需匹配 SDK 编译 IPK，不能安装 APK。

安装默认停用。进入“服务 → IPTV 回放与时移”，填写路由器和播放器均可访问的真实网关，启用并保存。默认 LAN 8088 独立于现有 8080 网关与 8081 扫描器。

## 配置订阅和诊断

订阅使用服务地址下的 `/IPTV-catchup.m3u`，直播为 `/IPTV.m3u`，源站直连为 `/gx.m3u`。酷9 时间格式与 Unix 占位符格式需按播放器选用；回放 UI 依赖客户端和 EPG。

组件生成 HLS 并以 302 让播放器请求原网关，不录制、不转码、不缓存视频。只适用于仍提供历史时间戳 TS 的源站。白名单保留期是配置上限，不保证全部历史可播放。

UCI 为 `/etc/config/iptv-catchup`，频道与回放配置在 `/etc/iptv-catchup/`。`/status` 和 JSON 日志可观察服务解析耗时，不能测量电视端完整下载和解码。服务无认证，保持可信 LAN 使用。

## 阅读源码快照

[luci-app-iptv-catchup/README.md](../luci-app-iptv-catchup/README.md) 是导出时纳入 `SOURCE.json` 摘要的原始文档，包含完整配置、SDK 构建及 0.1.0 的历史验证记录。其中“0.1.2 尚未发布”的句子描述导出时状态；实际可下载包以上面的 Release 为准。

更新导出快照应从 [my-IPTV](https://github.com/hellomrli/my-IPTV) 重新导出全部文件和摘要，避免只改快照文件导致校验失配。这次文档入口更新不代表在路由器或电视上重新验收。
