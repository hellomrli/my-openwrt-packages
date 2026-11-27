# 同步、覆盖与维护

[返回文档入口](index.md)

## 检查同步配置

源由 [sources.json](../sources.json) 声明，最新结果由脚本生成到 [SYNCED_SOURCES.md](../SYNCED_SOURCES.md)。本地准备 Python 3 和 Git；Honk 资源镜像还需要 GitHub 发布访问权限及脚本使用的 `gh`。

先做离线检查：

```sh
python3 -m json.tool sources.json >/dev/null
python3 -m unittest discover -s tests -v
```

真正同步会联网克隆、替换包目录，Honk 还可能上传 Release 资产，不是检查命令：

```sh
python3 scripts/sync-packages.py
```

## 理解同步结果和失败

普通源先在临时目录克隆、检查包目录并应用 overrides，忽略 `.git` 与 `.github` 元数据。只有准备完整的包才会替换镜像。

某个源失败时，该源的文件和来源记录保持不变；其他成功的源仍可提交。任务最终保持失败状态，日志列出故障源。若全部失败或来源表没有成功写完，不提交任何内容。来源记录不变时不会只更新时间戳。

Honk 通过 GitHub `releases/latest` 选择正式发布，支持 `v日期`、`v日期.序号` 和可选的 `-r数字` 后缀。脚本校验 manifest 的 `candidate_sha` 与 wrapper 标签实际 commit，再按 `source_tag` 取得 stage、provenance 和归档。不从标签猜测资源地址，也不因最新发布不兼容而静默退回旧版。

包版本、核心 commit、manifest 和 stage 中的摘要都须一致；缺失、不匹配或 `SKIP` 会中止该源，不回退滚动 staging。只补传已校验的缺失资源，不覆盖现有同名资源。

## 新增或移除源

1. 在 `sources.json` 写 `name`、`path`、`repo`、description，可选 branch。
2. 配套发布资源可使用 Honk 的 `honk-release` driver。成组导出时用 `exports` 替代 `path`，从一次克隆按「镜像目录：上游子目录」映射复制；其中一个包缺失，整组保留原版。
3. 同步后检查目录及来源表；固件使用还需修改该固件目标的清单和 `.config`。

本地维护包不要加入同步清单。仅更新 IPTV 扫描包可运行 `python3 scripts/sync-packages.py --only gxmobile`，不会触发 Honk 发布。移除源时，脚本不会自动删除清单外的目录。

## 长期覆盖上游文件

修改 [overrides](../overrides/README.md) 中对应副本，并说明偏离原因。直接修改同步目录会在下轮被覆盖。

Lucky 3.x 二进制来自官方文件服务，LuCI 和辅助脚本继续跟随其上游。固件构建前的 pin 脚本按官方校验清单刷新核心版本和逐架构 SHA256；镜像保存的是最近一次可用状态。

[Sync OpenWrt Packages](../.github/workflows/sync-packages.yml) 每 6 小时运行，支持手动触发。它先测试后同步，并通过 rebase/retry 合并并发的人类提交；遇到冲突会停止，不能盲目覆盖远端文档或包改动。
