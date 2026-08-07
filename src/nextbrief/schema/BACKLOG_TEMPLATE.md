---
# -- identity (written by a human; the agent must not change these) -----------
id: NA-0000
title: One sentence saying what to do (an action, not a goal)
project: <a project id from registry.jsonc>
type: task                    # feature | bug | task | chore | epic
# -- state --------------------------------------------------------------------
# "ready" is derived (open AND nothing blocking) and is never stored
status: open                  # open | in_progress | waiting | deferred | done | dropped
# done, dropped and deferred are HUMAN ONLY -- all three take the item off the
# page, and an agent that could park something could hide its own work.
# deferred is written by `nextbrief defer <id> --until ...`, never by hand:
deferred_until: null          # ISO date. The item is live again from this date on,
                              # with nothing written to bring it back
deferred_when: null           # the condition you are actually waiting on, when it
                              # is not a date ("after VirtualTutor ships")
deferred_because: null        # why it was put off
proposed_status: null         # an agent's SUGGESTION of a terminal status. It is
                              # listed in the brief under "waiting for your
                              # confirmation", and cleared the moment you answer
                              # with done / drop / ok
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

## The closing record — do not write this by hand

Written by `nextbrief done <id>`, which asks two questions and accepts an empty
answer to either. A new item does not have this block; it appears only once the
item is closed, at the very end of the file. There is no separate store, because
a done entry stays in `backlog/` forever and is already under version control.

`summary` is what ACTUALLY happened, which is frequently not what the title says
— an item reading "run 3 probes" whose truth was "migrated all of them" leaves
behind a false history if only the status is recorded. `future_work` is what
closing it turned up that does not belong to it; `nextbrief followup <id>` turns
any entry into a real item carrying `discovered_from` back to here, and writes
the resulting id beside the entry so an unpicked follow-up stays visible.

`summary_source` records whose sentence the summary is: `human` if you typed it
or passed `--summary`, `accepted_draft` if you took the draft `done` offered, and
`none` if you skipped the question. The draft is derived from the project's git
log and the acceptance ratio, and it is never what Enter means — Enter skips, `=`
takes the draft. A record closed before this field existed has no
`summary_source` line, which is not the same claim as `none`.

Read them across a project with `nextbrief closed [project]`. The keys are
English in every locale: they are parsed, not displayed.

```markdown
<!-- SECTION:CLOSING:BEGIN -->
closed_on: 2026-03-16
summary_source: human

summary: |
  What was actually done, and where it differed from what this item said.

future_work:
- Something this uncovered that is not this item's job
- Something already promoted -> NA-0042
<!-- SECTION:CLOSING:END -->
```
