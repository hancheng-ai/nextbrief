---
# -- identity (written by a human; the agent must not change these) -----------
id: NA-0000
title: One sentence saying what to do (an action, not a goal)
project: <a project id from registry.jsonc>
type: task                    # feature | bug | task | chore | epic
# -- state --------------------------------------------------------------------
# "ready" is derived (open AND nothing blocking) and is never stored
status: open                  # open | in_progress | waiting | done | dropped
priority: 2                   # 0-4, 0 highest. HUMAN ONLY
blocked_by: me                # me | agent | external-party | approval | decision | none
is_next_action: false         # at most one true per project (GTD). HUMAN ONLY
# -- the three-rung automation ladder ------------------------------------------
automation:
  tier: explore               # explore | skill | hook -- promotion depends on variance, not repetition count
  what_agent_can_do: the part an agent can take over
  what_needs_human: the step that cannot be subdivided further. If it can never be automated, say so
  next_probe: the cheapest experiment that would resolve "explore", with a duration
  assessed_on: 2026-03-16
  human_confirmed: false      # true freezes this whole block against the agent
# -- provenance ---------------------------------------------------------------
source:
  doc: path relative to the projects root
  anchor: which section or line of that document
  seen_on: 2026-03-16
  source_last_updated_declared: 2026-01-08   # the date the source claims for itself; the renderer marks the item stale from this
# -- metadata -----------------------------------------------------------------
estimate_min: 30
dependencies: []
discovered_from: null         # "found while doing X" -- this edge is what stops incidental findings from evaporating
created_date: 2026-03-16
updated_date: 2026-03-16
created_by: nextbrief-bootstrap   # nextbrief-bootstrap | nextbrief-daily | human
human_confirmed: false        # HUMAN ONLY. true means automatic decay can never touch this item
---

<!-- SECTION:NEXT_ACTION:BEGIN -->
One concrete physical action. "Open X and run Y", not "finish Z".
<!-- SECTION:NEXT_ACTION:END -->

<!-- AC:BEGIN -->
- [ ] #1 a verifiable completion condition
- [ ] #2 ...
<!-- AC:END -->

<!-- SECTION:NOTES:BEGIN -->
The daily pass replaces only what is inside this block; it never rewrites the
whole file. That is what stops an agent from "regenerating the document" and
quietly swallowing your annotations.
<!-- SECTION:NOTES:END -->
