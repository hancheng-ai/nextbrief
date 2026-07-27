---
id: NA-0003
title: Write the getting-started page a new contributor can follow unaided
project: tidepool-docs
type: task
status: open
proposed_status: done
proposed_by: nextbrief-daily
proposed_on: 2026-03-16
proposed_reason: handbook/getting-started.md now covers all four checklist steps and was last edited 2026-03-12; every acceptance criterion appears satisfied.
priority: 1
blocked_by: me
is_next_action: true
automation:
  tier: explore
  what_agent_can_do: Diff the page against the acceptance criteria and report which steps are still missing.
  what_needs_human: Following the page on a clean machine. Whether the instructions actually work is not decidable from the text.
  next_probe: Hand the page to somebody who has never set the project up and watch where they stop. 20 minutes.
  assessed_on: 2026-03-16
  human_confirmed: false
source:
  doc: tidepool-docs/HANDBOOK_STATUS.md
  anchor: "Gap 1: no entry point"
  seen_on: 2026-03-16
  source_last_updated_declared: 2025-11-02
estimate_min: 60
dependencies: []
discovered_from: null
created_date: 2026-02-24
updated_date: 2026-03-16
created_by: nextbrief-bootstrap
human_confirmed: false
---

<!-- SECTION:NEXT_ACTION:BEGIN -->
Confirm or reject the proposed completion: read handbook/getting-started.md against the three criteria below and set status yourself.
<!-- SECTION:NEXT_ACTION:END -->

<!-- AC:BEGIN -->
- [ ] #1 Every command in the page runs on a machine with nothing preinstalled
- [ ] #2 The page links onward to the style guide and to the migration plan
- [ ] #3 A first-time reader reaches a working local preview without asking anyone
<!-- AC:END -->

<!-- SECTION:NOTES:BEGIN -->
This is what a proposal looks like. The daily pass believes the work is finished and
says so in `proposed_status`, with a reason and a date. It cannot write
`status: done` -- only a human can, and the renderer rolls back any attempt.

The rule generalises: a false completion is far more damaging than a missed one. A
missed item stays visible and gets picked up eventually; a falsely closed item leaves
no trace, and the first sign of trouble is a contributor who cannot set the project up.

Note the staleness too. HANDBOOK_STATUS.md still declares 2025-11-02 while the files
around it moved this month, so its account of the gaps cannot be trusted as current.
<!-- SECTION:NOTES:END -->
