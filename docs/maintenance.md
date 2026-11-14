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

普通源克隆成功后才替换其目标目录，忽略 `.git` 与 `.github` 元数据，最后应用 overrides 并生成来源表。

每个包独立更新；整轮不是跨目录事务。某个上游失败会终止这一轮，前面已同步的目录可能已经变化，自动任务不会提交失败结果。上游长期消失时移除对应 `sources.json` 条目，使其他包继续更新，保留最后副本按维护需要决定。

Honk 同步读取最新正式发布 manifest，校验 `candidate_sha` 与 wrapper 标签实际 commit，再按 `source_tag` 取得 stage、provenance 和归档。发布标签的 `r` 后缀不一定是源码构建号，不要据此猜下载地址。

包版本、核心 commit、manifest 和 stage 中的摘要都须一致；缺失、不匹配或 `SKIP` 会中止，不回退滚动 staging。通过后镜像到 `honk-source-源码构建号` Release 并改写 URL。

## 新增或移除源

1. 在 `sources.json` 写 `name`、`path`、`repo`、description，可选 branch。
2. 只有配套发布资源需要特殊处理时才使用同步 driver；当前 Honk 使用 `honk-release`。
3. 同步后检查目录及来源表；固件使用还需修改该固件目标的清单和 `.config`。

本地维护包不要加入同步清单。移除源时，是否删除现有目录需明确执行；脚本不会自动清理清单外的目录。

## 长期覆盖上游文件

修改 [overrides](../overrides/README.md) 中对应副本，并说明偏离原因。直接修改同步目录会在下轮被覆盖。

Lucky 3.x 二进制来自官方文件服务，LuCI 和辅助脚本继续跟随其上游。固件构建前的 pin 脚本按官方校验清单刷新核心版本和逐架构 SHA256；镜像保存的是最近一次可用状态。

[Sync OpenWrt Packages](../.github/workflows/sync-packages.yml) 每 6 小时运行，支持手动触发。它先测试后同步，并通过 rebase/retry 合并并发的人类提交；遇到冲突会停止，不能盲目覆盖远端文档或包改动。
