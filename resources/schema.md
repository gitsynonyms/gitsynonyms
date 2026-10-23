# 资源登记表结构

`resources/registry.json` 是最高指示第三条的资源登记源，由资源获取分析司维护。
一条 URL 一条记录；钦定资源必须映射到真实存在的采集器。

## 顶层字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `schema` | 是 | 固定为 `gitsynonyms.resources.registry/1` |
| `updated_at` | 是 | 最近更新时间（ISO-8601 UTC） |
| `owner` | 是 | 固定为 `资源获取分析司` |
| `designated_by` | 是 | 指定钦定资源的层级，固定为 `总控` |
| `principle` | 是 | 固定为 `one-url-one-record` |
| `cadence_policy` | 是 | `floor` / `ceiling`，如 `5m`、`7d` |
| `resources` | 是 | 资源列表，一条 URL 一条记录 |

## 资源字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | 是 | 稳定标识，如 `yt:@WhiteHouse/streams` |
| `kind` | 是 | `youtube-channel` / `rss-feed` / `html-page` / `json-api` / `asset` / `account` / `harness` |
| `url` | 是 | 单个 http(s) 地址；不得包含多个 URL |
| `designated` | 是 | 是否钦定；钦定资源不得静默移除 |
| `trust` | 是 | `official` / `wire` / `press` / `aggregator` / `social` / `unknown` |
| `cadence` | 是 | 跟踪频率，落在 `cadence_policy` 区间内 |
| `analysis` | 是 | 该资源承担的分析用途，至少一项 |
| `collector` | 是 | `{script, source}`：采集脚本与其中的来源名，必须真实存在 |
| `status` | 是 | `active` / `degraded` / `retired` / `successor-pending` |
| `successor` | 否 | 迁移/改名后的资源 ID；`retired` 必填 |
| `note` | 否 | 补充说明 |

## 校验规则（`scripts/resources/check.py check`）

1. `schema`、`principle`、`owner`、`designated_by` 必须精确匹配。
2. 资源 `id` 唯一且符合 `^[A-Za-z0-9][A-Za-z0-9:@/._-]*$`。
3. `url` 必须是单个绝对 http(s) 地址；出现空白分隔的多个地址、逗号或分号即违规。
4. `kind`、`trust`、`status` 必须在枚举内；`cadence` 必须可解析且在策略区间内。
5. `analysis` 至少一项非空字符串。
6. `collector.script` 必须存在于仓库中，`collector.source` 必须存在于
   `scripts/monitoring/acquisition.py` 的 `SOURCES`，且该来源的 `url` 必须与资源
   `url` 完全一致——声明了却采不到的资源按登记不实处理。
7. 最高指示钦定的两条 YouTube 频道必须登记为 `designated: true` 且状态可跟踪；
   缺失、降级为普通资源或标记 `retired` 均违规。
8. `retired` 资源必须有 `successor`，且 `successor` 指向存在的资源 ID。

`scripts/resources/check.py coverage` 输出钦定资源覆盖率（可采集的钦定资源 / 全部
钦定资源）与按状态、按类型的分布。
