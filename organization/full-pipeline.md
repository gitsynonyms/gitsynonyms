# 全流程分支：端到端问题的专用作业面

> 依据：[`../DIRECTIVE.md`](../DIRECTIVE.md) 第二条（授权范围内的内部流程记入
> `organization/`）、第七条（证据与台账）、第九条（方案变更落文档）。
> 本页规定的是一条**工作分支的使用制度**，不新增部门、不改变层级、不改变巡检
> 粒度与节律，也不改变资源登记与发布授权。

## 1. 定位

分支名：`全流程`（远端 `origin/全流程`）。基线始终取 `main`。

该分支专门解决**全流程问题**：只有在"采集 → 下载 → 刻录 → 发布"整条链路串起来
才会暴露，或单模块自测通过但串联后失败的缺陷。其职责是**复现、定位、修复、端到端
验证**，不是新的常设组织单元，也不是第二条发布通道。

## 2. "全流程"的所指（四段链路）

仓库现有的端到端编排是 `scripts/process_video_pipeline.py`，其文档字符串定义为
`monitor → download → burn → publish`，退出码 0 表示全成功、1 表示任一环节失败：

| 段 | 动作 | 实现 | 成功判据 |
|---|---|---|---|
| 1 monitor | 经 yt-dlp 取元数据、校验 11 位视频 ID、查 JSONL 台账是否已处理 | `scripts/process_video_pipeline.py` | 元数据可解析；已入账且未 `--force` 时判定为"跳过"而非失败 |
| 2 download | 取 `zh-Hans` + `en-orig` 两条字幕与源视频 | `scripts/download_youtube_bilingual_srt.py` | 字幕目录与源视频齐备，原始字幕文件字节不变 |
| 3 burn | 生成精确 ASS、判定字幕区、单遍 libass 硬字幕，写 `BURN.json` / `TIMING.json` | `scripts/video_processing.py` | 刻录到临时文件并通过校验后原子替换；缺任一交接件即判失败 |
| 4 publish | 生成 Pages 归档页、上传成片到 GitHub Release、追加台账 | `scripts/generate_video_pages.py`、`scripts/process_video_pipeline.py` | 页面可索引、Release 资产存在、台账有且只有一条该视频记录 |

交接验收按 [`module-2-video-processing.md`](module-2-video-processing.md)：至少交付
最终 MP4、ASS、`BURN.json`、`TIMING.json` 与质检结果；caption-directory 模式另需
毫秒级 `*.precision.vtt`；归档索引用 `INFO.txt`。**不得把部分产物当作成功输出。**

云端入口为工作流 **Process Single Video (end-to-end)**：活动文件
`.github/workflows/process-single-video.yml`，受权限限制的留档模板
`.github/workflows-deploy/process-single-video.yml`；输入为 `video_url`、`output_dir`、
`state_file`、`languages`、`no_burn_in`、`no_pages`、`no_release`、`force`。

## 3. 什么算"全流程问题"

进入本分支的问题必须至少满足一条，并在记录里写明依据：

1. **跨段失败**：单段自测通过，串联后失败或产物不满足下游契约（典型：刻录成功但
   `BURN.json` 缺字段、页面索引不到成片）。
2. **交接件断裂**：MP4 / ASS / `BURN.json` / `TIMING.json` / 质检结果 / `INFO.txt`
   任一缺失、路径不符或格式漂移。
3. **环境态差异**：本地可跑、云端失败（字体缺失、工具版本、代理可达性、apt 包）。
4. **重复与幂等**：台账重复追加、已处理视频被重跑、半成品被当作成功发布。
5. **链路一致性**：模板与活动工作流漂移、部署步骤与代码不一致、文档承诺与实跑
   不符。
6. **发布闭环**：Pages 页面、Release 资产与台账三者不一致。

## 4. 不属于本分支的问题

- 单一脚本内部的样式、算法或性能优化：在模块自有分支处理；只有当它确实影响串联
  结果时才升格进本分支，并在记录里写清"为何单模块无法暴露"。
- 上游选题、频道扫描、资源登记与替代路径分析：属资源获取分析司。
- 巡检登记、频率、粒度、证据口径与异常分级：属定期巡检司与巡检任务执行方案处；
  本分支可以提交"需要新增/调整 MIU"的建议，但不得自行改结论。
- 任何试图降低巡检粒度、放宽频率或跳过交接件的"提效"：直接拒绝（最高指示 4.2、6）。

## 5. 作业规则

1. **先复现再修**：任何提交前必须先给出可重复的失败证据——云端 run 链接，或本地
   命令加输出摘要。复现不了就记为 `unknown`，不得猜因（最高指示第七条：采集失败
   本身也是结论）。
2. **失败阶段必填**：每条问题记录必须写明落在第 2 节的哪一段或哪个交接件。
3. **端到端验证**：修复后先 `no_burn_in=true` 快跑一次确认环境与下载段，再完整跑
   一次出成片；只跑单元测试不足以关闭全流程问题。
4. **不发布半成品**：失败时保留明确错误与失败阶段，不得把部分产物移到最终输出，
   不得用 `--force` 掩盖重复入账问题。
5. **权限边界**：本分支沿用 GitHub App 安装令牌，**没有 `workflows` 权限**，落在
   `.github/workflows/` 的提交会被服务端拒绝。改活动工作流必须
   `python scripts/monitoring/install_workflow.py process-single-video`（先看 diff
   再 `--force`），或按 `.github/workflows-deploy/process-single-video.HANDOFF.md`
   交给有权限的账号落地。
6. **模板不是真相源**：活动文件才是。模板与活动文件只允许存在交接单登记的差异，
   `tests/test_workflow_templates.py` 是漂移守卫；新增差异必须同步更新交接单与断言。
7. **不改他人结论**：本分支不得修改 `inspection/registry.json` 的巡检结论、
   `resources/registry.json` 的资源状态，也不得代替上游选片。

## 6. 分支操作约定

| 项目 | 约定 |
|---|---|
| 基线 | 每次开工前 `git fetch origin && git merge origin/main`（或 rebase），不长期落后 |
| 提交粒度 | 一个全流程问题一组提交，提交信息写"为什么 + 改了什么 + 怎么验证" |
| 合入方式 | 一律对 `main` 开 PR，禁止直推 `main`；需要真实 merge 记录，不 squash 掉验证过程 |
| 冲突处理 | 与 `main` 冲突时以 `main` 的巡检/资源/发布约束为准，本分支只保留端到端修复 |
| 分支寿命 | 问题闭环且 PR 合入后可删除；残留未闭环问题必须先转成带证据的记录，不得只留在分支上 |
| 备份 | 同一批提交同时推到 `origin/cline/ggvzxntr`，避免沙箱销毁丢失未推送工作 |

## 7. 完成定义（Definition of Done）

一条全流程问题只有同时满足下列各项才算关闭：

1. 失败阶段与根因写清，附复现命令或 run 链接；
2. 修复已推送，且端到端完整跑通一次（`no_burn_in=true` 快验 + 一次成片）；
3. 交接件齐全：MP4、ASS、`BURN.json`、`TIMING.json`、质检结果；caption-directory
   模式补 `*.precision.vtt`，归档补 `INFO.txt`；
4. 台账 `.github/state/whitehouse-processed-videos.txt` 只有一条该视频记录；
   若已发布，Pages 页面与 Release 资产与台账一致；
5. 相关测试通过（至少 `tests/test_process_video_pipeline.py`、
   `tests/test_video_processing.py`、`tests/test_workflow_templates.py`），
   并跑过 `python scripts/inspection/plan.py check --github` 与
   `python scripts/resources/check.py check`；
6. 结论落到本页的问题记录或对应交接单，不停留在对话里。

## 8. 已知结构性风险（非单次缺陷，需每次跑全流程时确认）

| 风险 | 表现 | 处置 |
|---|---|---|
| 令牌缺 `workflows` 权限 | 推送 `.github/workflows/` 被服务端拒绝 | 走 `install_workflow.py` 或交接单，由有权限账号落地 |
| 模板/活动文件漂移 | 模板改了但活动文件未跟进，云端仍跑旧版 | `install_workflow.py` 先看 diff；漂移守卫测试必须通过 |
| 云端缺 CJK 字体 | 中文行按缺字渲染，`BURN.json` 出现 `missing_glyphs` | 保持 `fonts-noto-cjk`、`fonts-dejavu-core` 安装步骤；改回只装 ffmpeg 即回归缺陷 |
| HLS 源不可达 | 经代理下载时 m3u8-only rendition 失败 | 保持 `--video-format` 的 https/DASH 优先级，不静默降级 |
| 台账重复/漏记 | 同一视频两条记录或成片已发布但无记录 | 以视频 ID 为键复核；异常按最高指示第八条分级上报 |

## 9. 与其他文档的关系

- 本页是**流程制度**，与 [`module-2-video-processing.md`](module-2-video-processing.md)
  的技术标准并行：技术标准定义"怎么算做对"，本页定义"全流程问题在哪条分支、按什么
  证据关闭"。
- 与 [`../README.md`](../README.md) 的端到端运行说明冲突时，以本页的分支与验收约定
  为准；与 [`../DIRECTIVE.md`](../DIRECTIVE.md) 冲突时，以最高指示为准。
