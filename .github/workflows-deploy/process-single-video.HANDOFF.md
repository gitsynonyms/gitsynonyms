# 交接：`process-single-video.yml` 合入 `main`

> 交付对象：有 `workflows` 权限的上级账号／合并人
> 来源分支：`cline/vbn0z4bm-media`（PR #29）
> 归档模板：`.github/workflows-deploy/process-single-video.yml`
> 目标路径：`.github/workflows/process-single-video.yml`
> 交付日期：2026-10-24

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

**A. 直接覆盖（推荐，一分钟）**

```bash
cd /workspaces/gitsynonyms
git checkout main && git pull
cp .github/workflows-deploy/process-single-video.yml .github/workflows/process-single-video.yml
git add .github/workflows/process-single-video.yml
git commit -m "Install CJK fonts and drop the dead style_preset input"
git push origin main
```

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

## 7. 回滚

```bash
git revert --no-edit <merge-or-commit>
```

或仅恢复字体行（改回 `sudo apt-get install -y ffmpeg`）。回滚不会影响已发布的
Release 资产与已归档字幕；只会让云端中文行重新缺字。
