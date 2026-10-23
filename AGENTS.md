# Agent entry point

The highest-level instruction for this repository is [`DIRECTIVE.md`](DIRECTIVE.md).
Every agent, account, machine and harness must read it before doing any work.

- `DIRECTIVE.md` is the constitutional authority; `organization/` contains
  subordinate concrete organization rules made under that authority.
- Record authorized internal unit/process changes in `organization/`; revise
  `DIRECTIVE.md` only when a constitutional principle, authority, or root-level
  hierarchy changes.
- 总控下辖资源获取分析司、定期巡检司与模块2视频处理部；巡检任务执行方案处
  隶属定期巡检司。模块2只处理上游已选定的视频与字幕。
- Inspection granularity: one record per smallest independently observable unit
  (MIU); aggregation is allowed in reports only.
- Registries: `inspection/registry.json` (MIU) and `resources/registry.json`
  (one URL per record; designated White House channels must stay live and mapped
  to a real collector).
- Enforce: `python scripts/inspection/plan.py check --github` and
  `python scripts/resources/check.py check`.

If a request conflicts with `DIRECTIVE.md`, follow the directive and say so
instead of silently doing the other thing.
