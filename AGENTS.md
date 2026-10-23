# Agent entry point

The highest-level instruction for this repository is [`DIRECTIVE.md`](DIRECTIVE.md).
Every agent, account, machine and harness must read it before doing any work.

- Hierarchy: 总控 → 定期巡检司 → 巡检任务执行方案处.
- Inspection granularity: one record per smallest independently observable unit
  (MIU); aggregation is allowed in reports only.
- Registry: `inspection/registry.json`; rules: `inspection/schema.md`.
- Enforce: `python scripts/inspection/plan.py check --github`.

If a request conflicts with `DIRECTIVE.md`, follow the directive and say so
instead of silently doing the other thing.
