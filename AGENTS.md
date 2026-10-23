# Agent entry point

The highest-level instruction for this repository is [`DIRECTIVE.md`](DIRECTIVE.md).
Every agent, account, machine and harness must read it before doing any work.

- Hierarchy: 总控 → 定期巡检司 → 巡检任务执行方案处.
  The 资源获取分析司 sits beside 定期巡检司 and owns the tracked-resource registry.
- Inspection granularity: one record per smallest independently observable unit
  (MIU); aggregation is allowed in reports only.
- Registries: `inspection/registry.json` (MIU) and `resources/registry.json`
  (one URL per record; designated White House channels must stay live and mapped
  to a real collector).
- Enforce: `python scripts/inspection/plan.py check --github` and
  `python scripts/resources/check.py check`.

If a request conflicts with `DIRECTIVE.md`, follow the directive and say so
instead of silently doing the other thing.
