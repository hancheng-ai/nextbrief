---
id: NA-0001
title: Split the tenancy latency report per tenant
project: orchard
status: open
priority: 2
is_next_action: true
human_confirmed: false
created_by: human
estimate_min: 45
updated_date: 2026-03-16
tags: [latency, tenancy, bench]
automation:
  tier: hook
  what_agent_can_do: Re-run the harness and group the timings by tenant
  what_needs_human: Decide whether the tail matters more than the mean
  next_probe: Read one existing result file and check it records a tenant id
source:
  doc: orchard/PROJECT_STATUS.md
  anchor: "## Results"
  source_last_updated_declared: 2026-03-10
---

# Notes

The aggregate report averages the tail away, which is the part the decision
turns on.

## Done when

- [ ] p95 is reported per tenant
- [ ] the old aggregate is still available
