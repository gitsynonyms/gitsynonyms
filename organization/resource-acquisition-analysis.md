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
| 活动工作流 | `.github/workflows/infogap.yml` |
| 工作流模板 | `.github/workflows-deploy/infogap.yml` |
| 发布指南 | `.github/workflows-deploy/README.md` |
| 输出 | `site/infogap/index.html`、`report.json`、按日期归档的历史 JSON |

活动工作流每 6 小时运行并支持手动触发；两个触发方式已分别登记为最小可巡检单元。
归档模板用于维护和复用，不单独触发运行。公开研究和抓取仅在 GitHub-hosted runner
上执行。

## 代理全量扫描与 top10 发布（网络出口保障）

本处负责素材获取所需的公共网络出口保障。出口候选的发现、采集、验证、测速与发布
固化为单一流程 `proxyindex/full_scan.py`，由资源获取分析司维护、由巡检司按
`gh:gitsynonyms/proxy-full-scan` 巡检。

| 项目 | 映射 |
| --- | --- |
| 检索词矩阵 | `proxyindex/query_matrix.py`（中英文同义词/近义词全排列） |
| 全量流程 | `proxyindex/full_scan.py` |
| 调度 | `scripts/monitoring/proxy-scan.yml`（每小时 + 手动，源文件；经 `install_workflow.py` 安装为活动工作流） |
| 发布物 | `proxyindex/top10.json`、`top10.txt`、`scan-report.md`、`scan-state.json` |
| 消费方 | `scripts/monitoring/acquisition.py`、`scripts/select_public_ytdlp_proxies.py` |

规则：

1. 全量口径是"矩阵 + list² 来源"，不得以"三个列表各取 10 个"替代。
2. 单轮受时间预算约束时，必须写出游标与覆盖率；未探测候选记为 `unknown`，不得计入可用。
3. 采集失败按 `missing`（永久）与 `error`（18 小时后重试）分别登记，不得静默丢弃来源。
4. 发布前必须对候选做一次立即复验；只有复验通过的 10 个可以进入 `top10.json`。
5. 发布物同步到云端仓库 `main`；推送失败按 P2 处置，不得改写已发布结果。活动工作流
   安装前，扫描跟随 `monitor-whitehouse.yml` 的巡检单元执行，独立工作流安装后再登记
   `gh:gitsynonyms/proxy-full-scan`。
6. 只采集 `ip:port` 形式的 HTTP 代理，不采集 SOCKS 与订阅明文，不在任何公共代理上传输凭据、Cookie 或密钥。
