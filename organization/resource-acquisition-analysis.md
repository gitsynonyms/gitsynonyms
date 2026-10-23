# 资源获取分析司：信息差公示处

本文件是 `DIRECTIVE.md` 授权范围内的具体组织规章。它展开资源获取分析司的内部
职责，不改变根本层级、资源登记原则或巡检权限。

## 隶属与职责

信息差公示处隶属资源获取分析司，按上游登记的研究主题，调用
`last30days-skill` 汇总公开来源，标记来源缺口、叙述差异和置信度，并输出静态
HTML 与 JSON 报告。默认计划周期为 6 小时，另支持手动运行。

本处不选择模块2的视频素材、不代替巡检司判定健康、不提出政策建议，也不把无数据
的结果伪装成事实。低置信度、缺失依赖或研究失败须标记为降级；严格质量门禁失败时
不得覆盖已发布的 Pages 内容。

## 资源与实现映射

| 项目 | 映射 |
| --- | --- |
| 资源登记 | `resources/registry.json` 中的 `harness:infogap-last30days` |
| 生成器 | `scripts/infogap/generate.py` |
| 工作流模板 | `.github/workflows-deploy/infogap.yml` |
| 发布指南 | `.github/workflows-deploy/README.md` |
| 输出 | `site/infogap/index.html`、`report.json`、按日期归档的历史 JSON |

工作流模板需复制到 `.github/workflows/` 才会运行。启用后，定时与手动触发分别作为
两个最小可巡检单元登记；模板归档目录本身不构成活动工作流。公开研究和抓取仅在
GitHub-hosted runner 上执行。
