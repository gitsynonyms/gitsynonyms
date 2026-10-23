# 巡检登记表结构

`inspection/registry.json` 是最高指示第四条的巡检登记源。`scripts/inspection/plan.py`
对它的校验就是"最小粒度"的可执行定义：一条记录只能对应一个 MIU，缺口必须显式声明。

## 顶层字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `schema` | 是 | 固定为 `gitsynonyms.inspection.registry/1` |
| `updated_at` | 是 | 最近一次登记的 ISO-8601 UTC 时间 |
| `directive` | 是 | 指向最高指示文件 |
| `principle` | 是 | 固定为 `minimum-granularity` |
| `cadence_policy` | 是 | `floor` / `ceiling` / `default`，均为 `5m`、`1h`、`1d` 形式 |
| `supported_mechanisms` | 是 | 本体系覆盖的全部机制；每个机制都必须有单元或缺口 |
| `units` | 是 | MIU 列表，每个 MIU 一条 |
| `gaps` | 是 | 尚未完成盘点的机制，必须写明原因与下一步动作 |

## 单元字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | 是 | 稳定标识，如 `gh:owner/repo-workflow`、`cline:acct/task-id` |
| `mechanism` | 是 | 必须属于 `supported_mechanisms` |
| `title` | 是 | 人类可读名称 |
| `target` | 是 | 定位该单元的属性集合；值只能是标量，不能是数组 |
| `trigger` | 是 | `schedule` / `workflow_dispatch` / `push` / `pull_request` / `manual` / `timer` / `login` / `boot` / `unknown` |
| `cadence` | 是 | 单单元频率，必须落在 `cadence_policy` 的 floor 与 ceiling 之间 |
| `severity` | 是 | `P0`–`P3`，含义见最高指示第八条 |
| `owner` | 是 | 负责该单元的执行方 |
| `status` | 是 | `active` / `paused` / `retired` / `pending-inventory` |
| `evidence` | 是 | 可直接复现的采集命令或链接，至少一条 |
| `note` | 否 | 补充说明 |

## 校验规则（`plan.py check`）

1. `schema`、`principle` 必须精确匹配；`cadence_policy` 必须可解析且 floor ≤ ceiling。
2. `supported_mechanisms` 不得为空或重复。
3. 单元 `id` 唯一且符合 `^[a-z0-9][a-z0-9:/._-]*$`。
4. `target` 必须非空、值只能是标量；出现数组、`*`、`all`、`any`、`全部` 或逗号
   视为把多个单元合并为一条，直接判违规。
5. `mechanism`、`trigger`、`severity`、`status` 必须在枚举内。
6. `cadence` 必须可解析并落在策略区间内。
7. `evidence` 至少一条非空字符串。
8. 每个 `supported_mechanisms` 中的机制，必须至少有一个单元，或在 `gaps` 中声明；
   同一机制既有活跃单元又有缺口，视为登记矛盾。
9. 缺口必须写明 `reason` 与 `action`。

`plan.py check --github` 还会用 `gh workflow list` 对账：GitHub 上存在但登记表没有的
工作流、或登记了但 GitHub 上不存在的工作流，都算违规；无法访问 GitHub 时降级为
警告（`--strict` 下仍失败）。

## 缺口与覆盖

`gaps` 是"已知未覆盖"，不是豁免。`plan.py coverage` 会按
`已覆盖机制 / 全部机制` 计算覆盖率，并把每个缺口的机制与动作打印出来。
缺口关闭的唯一方式是在 `units` 中补登记，并删除对应缺口。
