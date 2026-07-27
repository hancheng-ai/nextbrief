---
id: NA-0001
title: Re-run the tenancy benchmark reporting p95 per tenant instead of aggregated
project: orchard-api
type: task
status: open
priority: 0
blocked_by: me
is_next_action: true
automation:
  tier: hook
  what_agent_can_do: Change the reporter to group by tenant_id and emit p50/p95/p99 per tenant for the ten largest by row count; the harness already records per-request timings, so nothing new has to be measured.
  what_needs_human: Reading the resulting tail and deciding whether it justifies per-tenant schemas. That is a product judgement, not a query.
  next_probe: "python bench/harness.py --report --group-by tenant --top 10" against the existing results/ directory -- one run, no new data collection.
  assessed_on: 2026-03-16
  human_confirmed: false
source:
  doc: orchard-api/docs/TENANCY_DECISION.md
  anchor: "Section 4, Open questions"
  seen_on: 2026-03-16
  source_last_updated_declared: 2026-03-11
estimate_min: 45
dependencies: []
discovered_from: null
created_date: 2026-03-16
updated_date: 2026-03-16
created_by: nextbrief-bootstrap
human_confirmed: false
---

<!-- SECTION:NEXT_ACTION:BEGIN -->
Re-run the existing benchmark results through a per-tenant grouping and look at the p95 for the ten largest tenants.
<!-- SECTION:NEXT_ACTION:END -->

<!-- AC:BEGIN -->
- [ ] #1 A table of p50/p95/p99 per tenant, covering the ten largest tenants
- [ ] #2 The question "does the tail get worse with tenant size" is answered yes or no
- [ ] #3 The answer is written into TENANCY_DECISION.md and the decision is either taken or explicitly deferred with a date
<!-- AC:END -->

<!-- SECTION:NOTES:BEGIN -->
The blocker is not capacity, it is that the evidence is already on disk and nobody
has looked at it in the shape that would settle the argument. `bench/results/*.json`
carries per-request timings including `tenant_id`; the reporter averages them into a
single number, which is precisely the number that cannot distinguish the two designs.

BENCH_NOTES.md still says the benchmark is unfinished. PROJECT_STATUS.md says it
completed on 2026-03-09 and has higher authority, so the registry resolves the
conflict in favour of PROJECT_STATUS.md. Treat BENCH_NOTES.md as out of date.
<!-- SECTION:NOTES:END -->
