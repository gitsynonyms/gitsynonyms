# 交接：`process-single-video.yml` 合入 `main`

> 交付对象：有 `workflows` 权限的上级账号／合并人
> 来源分支：`cline/vbn0z4bm-media`（PR #29）
> 归档模板：`.github/workflows-deploy/process-single-video.yml`
> 目标路径：`.github/workflows/process-single-video.yml`
> 交付日期：2026-10-24

## 0. 待办清单（给有权限的账号）

| # | 动作 | 需要什么权限 | 命令 / 位置 |
|---|---|---|---|
| 1 | 合并 PR #29 | 普通 merge（**不需要** `workflows`） | <https://github.com/gitsynonyms/gitsynonyms/pull/29>；合入后 Pages 自动发布 `videos/lnOiG5dI4hg.html` |
| 2 | 安装工作流模板 | `workflows` | `python scripts/monitoring/install_workflow.py process-single-video` 先看差异（预期恰为第 3 节 4 个块），再 `--force` 安装、commit、`git push origin main` |
| 3 | 触发一次验证 | Actions 运行权限 | `Actions → Process Single Video (end-to-end) → Run workflow`，先 `no_burn_in=true`（约 5 分钟，只验证字体安装步骤），需要成片时再跑全流程 |
| 4 | 回报 | — | 回传 ① 合入 commit SHA ② 工作流 run 链接 ③ 第 3 步日志里 `fonts-noto-cjk`/`fonts-dejavu-core` 安装成功的那几行 |
| 5 | （可选，一次性） | App 所有者 | 给 App 勾 **Repository permissions → Workflows: Read and write**，并在组织安装处 Approve；之后这类补丁我可以直接推 |

第 2 步的完整命令见第 2 节；安装器不接受静默覆盖，先看差异再 `--force`。

## 1. 为什么由你合入

本分支的令牌是 GitHub App 安装令牌，没有 `workflows` 权限，任何落在
`.github/workflows/` 的提交都会被服务端拒绝：

```text
! [remote rejected] ... (refusing to allow a GitHub App to create or update
workflow `.github/workflows/process-single-video.yml` without `workflows` permission)
```

已用探针分支 `probe/workflow-perm` 复验一次，仍然被拒。App 权限归平台方，不在本仓库
可控范围，因此按既有约定（见本目录 `infogap.yml`）**把改好的活动工作流整份归档到本目录**，
由有权限者负责落地到 `.github/workflows/`。

## 2. 合入方式（二选一）

**A. 用仓库自带的安装器（推荐，一分钟）**

`scripts/monitoring/install_workflow.py` 就是这套“受权限限制的模板 → 活动工作流”的既有工具
（`monitor`／`proxy-scan` 一直这么装），`process-single-video` 已登记进去。它会先拒绝对已漂移的
活动文件做静默覆盖：

```bash
cd /workspaces/gitsynonyms
git checkout main && git pull

# 第一步：不带 --force，它会打印活动文件与模板的差异
python scripts/monitoring/install_workflow.py process-single-video
#   -> 退出码 1，差异应恰好是第 3 节的 4 个块；不是就停下来找我重切模板

# 第二步：确认后强制安装并发布
python scripts/monitoring/install_workflow.py process-single-video --force
git add .github/workflows/process-single-video.yml
git commit -m "Install CJK fonts and drop the dead style_preset input"
git push origin main
```

`all` 只覆盖已稳定的 `monitor`／`proxy-scan`，不会顺手把这个模板推进去。

**B. 只取正文，人工比对**

```bash
diff -u .github/workflows/process-single-video.yml \
        .github/workflows-deploy/process-single-video.yml
```

合入后请确认 `inspection/registry.json` 无需新增 MIU：本次只改既有工作流的两个步骤，
触发方式（`workflow_dispatch`）与输入项集合未新增，只删除了一个已废弃输入
（`style_preset`，代码侧已弃用并告警，删掉不改变任何产出口径）。

## 3. 变更内容（仅 3 处）

```diff
@@ on.workflow_dispatch.inputs
-      style_preset:
-        description: ASS style preset
-        required: false
-        type: string
-        default: "classic"

@@ jobs.process.steps[Install yt-dlp and FFmpeg]
-          sudo apt-get update -y && sudo apt-get install -y ffmpeg
+          sudo apt-get update -y
+          # libass resolves the ASS families through fontconfig: without the CJK and
+          # Latin packages the bilingual Chinese line burns in as missing glyphs.
+          sudo apt-get install -y ffmpeg fonts-noto-cjk fonts-dejavu-core

@@ jobs.process.steps[Run end-to-end pipeline].env / ARGS
-          STYLE_PRESET: ${{ inputs.style_preset }}
-            --style-preset "$STYLE_PRESET"
```

为什么要装字体：模块2的刻录用 `libass` + `fontconfig` 解析 ASS 的
`Microsoft YaHei` / `Arial`。ubuntu-hosted runner 默认没有任何 CJK 字体，中文行会按缺字
渲染。本地复现时在 `~/.fonts` 放入 Noto Sans CJK SC + DejaVu Sans 后才得到正常的
金色中文／白色英文；`BURN.json` 记录 `missing_glyphs: 0`、`fontselect: (Microsoft YaHei)
-> NotoSansCJKsc-Bold`、`(Arial) -> DejaVuSans`。`fonts-noto-cjk` 提供的正是同一族
（Noto Sans CJK SC），`fonts-dejavu-core` 提供拉丁回退。

`style_preset` 删除的依据：`scripts/process_video_pipeline.py` 已把刻录交给
`scripts/video_processing.py`，ASS 由模块2自行生成，下载器的样式预设不再到达刻录环节，
参数只剩误导性。代码侧保留该参数但打印弃用告警，因此“代码已合、workflow 未合”期间
工作流依旧可跑（只是多一行告警）。

## 4. 同批次的程序改动（已在 PR #29 内，与本工作流无耦合）

| 文件 | 改动 |
|---|---|
| `scripts/generate_video_pages.py` | 新增 `poster_name()` / `poster_attribute()`：按 `site/media/<video_id>-poster.<ext>` 逐视频取封面，文件不存在则不写 `poster=` |
| `scripts/process_video_pipeline.py` | 新增 `write_site_poster()`：刻录成功后按时长 10%（夹在 5–60 s）抽一帧写入 `site/media/`；失败仅告警 |
| `tests/test_generate_video_pages.py` | 覆盖逐视频封面查找与 `poster=` 省略 |
| `site/media/` | 圆桌封面改名为 `TLH3gXrPCTU-poster.jpg`；新增 `lnOiG5dI4hg-poster.jpg` |
| `docs/images/` | 新增 `lnOiG5dI4hg-615s.jpg`、`-1100s.jpg` 抽帧证据 |
| `README.md` | 新增「成片封面（图片目录）与抽帧复核」章节 |

上一批次（PR #28，已合入 main）的改动供对照：端到端流程改为由
`video_processing.py` 负责刻录与 `BURN.json`/`TIMING.json` 交接、补写 `INFO.txt`、
`--video-format` 优先 https/DASH、修 `require_tool`/`run(env=)`/`append_ledger` 三处必崩缺陷。

## 5. 模型与素材改动

- **模型：无改动。** 未更换 ASR、未更换翻译模型、未引入本地推理；字幕仍来自 YouTube
  `en-orig`（自动英文字幕）+ `zh-Hans`（自动翻译），逐词时间来自 `json3`，七格式融合。
- **素材：** 仅新增/重命名站点封面与 README 抽帧图（PNG/JPG，合计约 450 KB），不含视频。
- **依赖：** 云端仅新增两个 apt 字体包；Python 侧无新依赖。

## 6. 验证证据

```text
python -m pytest tests -q --ignore=tests/test_gitsynonyms.py \
  --ignore=tests/test_youtube.py --ignore=tests/test_youtube_archive.py
296 passed

python scripts/generate_video_pages.py
videos found: 5
poster="../media/lnOiG5dI4hg-poster.jpg"      # 详情页
poster="media/lnOiG5dI4hg-poster.jpg"         # latest.html
poster="../media/TLH3gXrPCTU-poster.jpg"      # 圆桌自己的封面
# 其余 3 个视频无 poster=（不再借用他人封面）

ffprobe（lnOiG5dI4hg 成片）: h264 1920x1080 29.97 + aac, duration 1336.454 s
BURN.json: status=burned, caption box auto => none（干净片源）, missing_glyphs=0
TIMING.json: 7 格式融合, 12,707 timestamps / 10,315 逐词, ASS 量化误差 max 5 ms / mean 1.099 ms
```

工作流语法：本文件与活动文件除上述 3 处外逐字一致，合入后可由
`Actions → Process Single Video (end-to-end) → Run workflow` 直接验证（建议先用
`no_burn_in=true` 跑一次，确认字体安装步骤成功再跑全流程）。

## 8. 复盘：合入方式与模板漂移（2026-10-24 学习结果）

**A. 合入方式（观察自 PR #28）**

```text
44c645258b990d90fd6da72c0f745d7bea1203b9
parents: 7e8e36f0ad7fa58a89e9f3489f1316db15222898 38d656f0ca81aa676b15fd40a475186b54c6a982
author: gitsynonyms   committer: GitHub
Merge pull request #28 from gitsynonyms/cline/vbn0z4bm
```

`git diff 38d656f origin/main` 为空：**真 merge、未 squash、无合并期编辑**，我的两个提交与文件
内容逐字节进入 main。结论：提交信息与交接单本身就是永久记录，不依赖对话上下文；后续提交
信息保持“为什么 + 改了什么 + 怎么验证”的结构。

**B. PR #29 尚未合入（截至本复盘）**

`origin/main` 仍停在 `44c6452`，活动工作流仍是旧版（含 `style_preset`、无字体安装），
`scripts/generate_video_pages.py` 也还没有 `poster_name()`。所以“封面分档 + 字体”两块
都还在本分支上等合入；本文件的安装步骤对旧版活动文件成立。

**C. 教训：模板不是真相源，活动文件才是**

既有先例 `.github/workflows-deploy/infogap.yml` 已经和 `.github/workflows/infogap.yml`
漂移（cron 注释、`pip install` 步骤写法、多余空行三处），说明“模板留档 + 事后 cp”会
让模板悄悄过期。因此本交接单额外约束：

1. **模板切点**：`44c6452`（本文件与 `.github/workflows-deploy/process-single-video.yml`
   都从该提交的活动工作流切出）。
2. **安装前先看差异**：`diff -u .github/workflows/process-single-video.yml .github/workflows-deploy/process-single-video.yml`
   —— 若出现本节 3 处以外的差异，说明活动文件已前进，**只打 3 个 hunk**，不要 `cp` 覆盖。
3. **漂移守卫**：`tests/test_workflow_templates.py::test_staged_template_only_differs_by_the_documented_changes`
   断言“活动文件与模板的差异恰为下面 4 个块（3 处变更，其中字体行是 1 删 4 增）”。
   实测注入一行无关改动即失败，合入后两文件一致时该断言自动通过。

```text
块 1: 删 style_preset input（5 行）
块 2: 删 sudo apt-get update -y && ... ffmpeg  →  增 update -y + 2 行注释 + 装字体行
块 3: 删 STYLE_PRESET env
块 4: 删 --style-preset "$STYLE_PRESET"
```

复盘结论按最高指示第九条落到本文件与该目录 `README.md`，不停留在对话里。

## 9. 回滚

```bash
git revert --no-edit <merge-or-commit>
```

或仅恢复字体行（改回 `sudo apt-get install -y ffmpeg`）。回滚不会影响已发布的
Release 资产与已归档字幕；只会让云端中文行重新缺字。
