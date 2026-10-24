# 信息差公示处 GitHub Actions 部署指南

此文件夹是工作流模板归档目录；活动工作流位于
`.github/workflows/infogap.yml`，每 6 小时运行并可手动触发。所有在线研究与抓取仅在
GitHub-hosted runner 上执行。

## 文件位置

- **工作流源文件**: `.github/workflows-deploy/infogap.yml`
- **活动工作流**: `.github/workflows/infogap.yml`
- **生成脚本**: `scripts/infogap/generate.py`
- **输出目录**: `site/infogap/`

## Pages 设置

### 1. 启用 GitHub Pages

进入仓库设置：
```
Settings → Pages → Build and deployment
Source: GitHub Actions
```

The active workflow and its schedule/manual triggers are already registered as
separate GitHub workflow MIUs in `inspection/registry.json`.

## 工作流说明

| 触发方式 | 说明 |
|---|---|
| `schedule: 0 */6 * * *` | 每 6 小时自动运行 |
| `workflow_dispatch` | 手动触发，可指定 topics |

## 产出物

| 文件 | 说明 |
|---|---|
| `site/infogap/index.html` | 交互式报告页 |
| `site/infogap/report.json` | 机器可读结构化数据 |
| `site/infogap/history/YYYY-MM-DD.json` | 历史快照 |

The workflow runs `scripts/resources/check.py check` and
`scripts/inspection/plan.py check --github` in its validation job before
research. Research failures or a degraded report block publication; the previous
Pages deployment remains in place.

## 本地 checks

只运行离线单元测试时，在 Windows 使用 Anaconda Python：

```powershell
$python = Join-Path $env:USERPROFILE "anaconda3\python.exe"
& $python -m pytest -q tests\test_infogap_generator.py tests\test_resources_check.py
```

生成器实际联网研究由 GitHub runner 执行；不要在本机运行在线采集命令。
上游 `last30days-skill` 的可用来源和认证要求以其文档为准，本工作流不伪称
启用了未配置的 API key 或本地评分模型。

## 其他已归档模板

| 模板 | 目标活动工作流 | 说明 |
|---|---|---|
| `process-single-video.yml` | `.github/workflows/process-single-video.yml` | 单视频端到端（monitor → download → burn → publish）；改动与合入步骤见 [`process-single-video.HANDOFF.md`](process-single-video.HANDOFF.md)。改动仅 3 处：安装 `fonts-noto-cjk`/`fonts-dejavu-core`、删除已废弃的 `style_preset` 输入及其 env/CLI 透传。 |
| `scripts/monitoring/proxy-scan.yml` | `.github/workflows/proxy-scan.yml` | 代理全量扫描改为分片多 runner；源文件已更新、活动文件待安装，步骤见 [`proxy-scan.HANDOFF.md`](proxy-scan.HANDOFF.md)。安装需 `--force`，因为活动文件仍是旧的单 runner 版本。 |

## 模板漂移与安装前检查

模板不是真相源：`.github/workflows/` 里的活动文件才是。既有先例 `infogap.yml` 已经与
活动文件漂移（cron 注释、`pip install` 步骤写法、多余空行），说明“留档 + 事后盲 `cp`”
会让模板悄悄过期、并回退别人对活动文件的改动。因此：

```bash
# 安装前先看差异：不带 --force 时安装器会拒绝覆盖并打印 diff
python scripts/monitoring/install_workflow.py process-single-video
# 差异确认无误后再安装
python scripts/monitoring/install_workflow.py process-single-video --force
```

- `process-single-video.yml` 的模板切点是 `44c6452`（交接单第 8 节）。
- 漂移守卫：`tests/test_workflow_templates.py::test_staged_template_only_differs_by_the_documented_changes`
  断言活动文件与模板的差异恰为交接单记录的 4 个块；活动文件一有无关改动即失败。
- 合入后两文件一致，该断言自动通过，此后模板随活动文件同步更新即可。


