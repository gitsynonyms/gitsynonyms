# 信息差公示处 GitHub Actions 部署指南

此文件夹是工作流模板归档目录；GitHub 只会执行 `.github/workflows/` 中的
工作流。模板部署后每 6 小时运行，并可手动触发。所有在线研究与抓取仅在
GitHub-hosted runner 上执行。

## 文件位置

- **工作流源文件**: `.github/workflows-deploy/infogap.yml`
- **目标部署位置**: `.github/workflows/infogap.yml`
- **生成脚本**: `scripts/infogap/generate.py`
- **输出目录**: `site/infogap/`

## 部署步骤

### 1. 启用 GitHub Pages

进入仓库设置：
```
Settings → Pages → Build and deployment
Source: GitHub Actions
```

### 2. 安装工作流

在仓库根目录执行：

```powershell
New-Item -ItemType Directory -Force .github\workflows | Out-Null
Copy-Item .github\workflows-deploy\infogap.yml .github\workflows\infogap.yml
```

The copied workflow needs to be committed and pushed before GitHub can run it.
If enabled, register its scheduled and manual trigger as two separate GitHub
workflow MIUs in `inspection/registry.json`; the inspection checker treats each
trigger mode independently.

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

If the workflow is copied into `.github/workflows/`, it becomes active; keeping
it in this archive directory alone does not schedule or deploy anything.
