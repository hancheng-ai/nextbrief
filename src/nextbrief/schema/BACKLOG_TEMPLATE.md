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
                              # is not a date ("after Fernwood ships")
deferred_because: null        # why it was put off
proposed_status: null         # an agent's SUGGESTION of a terminal status. It is
                              # listed in the brief under "waiting for your
                              # confirmation", and cleared the moment you answer
                              # with done / drop / ok
priority: 2                   # 0-4, 0 highest. HUMAN ONLY
blocked_by: me                # me | agent | external-party | approval | decision | none
is_next_action: false         # at most one true per project (GTD). HUMAN ONLY
# Written by `nextbrief do <id>` when it opens a session, never by hand. See
# "The claim record" below: it is a note saying somebody started, not a lock.
claim:
  by: whoever ran `do`, under the git identity this workspace commits with
  at: 2026-03-16               # the day the session was opened
  where: the directory the session was opened in
  branch: the branch that directory was on, or null when it is not a repository
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
- [ ] #1 (agent) a verifiable completion condition
- [ ] #2 (you) one only you can settle
<!-- AC:END -->

<!-- SECTION:NOTES:BEGIN -->
The daily pass replaces only what is inside this block; it never rewrites the
whole file. That is what stops an agent from "regenerating the document" and
quietly swallowing your annotations.
<!-- SECTION:NOTES:END -->

## Acceptance criteria — three marks and one label

`- [ ]` open · `- [x]` done · `- [~]` the design moved past this one. The third is
written by `-` in the `done` selector, never by hand and never by an agent. It is a
mark, not a deletion: the line and its sentence stay, because erasing them would
erase the one fact worth keeping, which is that the goal moved. Dropped criteria
stay in the denominator and are reported beside it (`1/3 ticked, 1 dropped`), and
they are never drafted as future work — nobody is meant to pick them up.

`(agent)` or `(you)`, right after the number, says **who can tell that it is
true** — not who does the work. Those come apart constantly: only you can choose
the illustrations, but "three files appeared in `assets/`" is something one
command can see, so that criterion is the agent's. `(agent)` is the default.
Reserve `(you)` for what only you can settle: direction, UAT, access, resources an
agent cannot obtain, and your own judgement as the user.

`done` asks you about the `(you)` ones first, then about any the agent's that
nothing settled, in a list of their own (`--all-criteria` asks about all of them
in one list). Enter leaves them open.
`check` warns when an item has more than two on you, or has criteria carrying no
label at all. An unlabelled criterion is treated as yours, because nobody has said
otherwise yet.

## The claim record — a note, not a lock

`nextbrief do <id>` sets `status: in_progress` and writes the `claim` block above
before it hands the terminal to an agent. Nothing else writes either of them, and
nothing anywhere refuses on the strength of them.

Run `do` on an item that already carries a claim and it prints the claim exactly
as it stands in the file, then asks — and *carrying on is allowed*, after which
the claim is replaced by yours. That is deliberate. The failure this was written
for is a session that went idle still holding the work, three times out of three;
an enforcing lock's answer to that is to seal the item shut, and the item you can
no longer touch is precisely the one somebody needs to pick up. A stale claim
tells you something and never stops you.

`nextbrief check` warns when an item has been claimed since before today, the
claim was made on a branch somebody created on purpose, and that branch has had
no commit since `claim.at` — the one reading that would have caught the idle
session the morning after instead of two days later. It stays quiet on a claim
taken today, on a branch with commits on it, and on any claim it cannot check (no
branch recorded, no git, a directory that has gone), because a warning fired on
absent evidence teaches you to ignore the warning.

**It also stays quiet on every claim made on the repository's trunk, and that is
the boundary worth knowing about.** The question it asks — has this branch had a
commit — is one that anybody else's push answers for you, so on a shared branch
the answer is about the repository rather than about your item. Replaying a real
portfolio's whole backlog against the history of the repositories each item was
worked in, claiming each on the day its work started, put **92% of claims on the
trunk**, and **51% of all claims were silenced by commits that had never touched
the item**. Restricting the warning to dedicated branches cut it from 18 firings
to 4 over that fortnight — ten of the eighteen had been fired at items that were
already finished — while still catching the one real abandonment on record.

Starting an item on the trunk is the most common way to start one, so state the
consequence plainly: **this warning is a net under dedicated branches only, and
its silence is never evidence that an item is progressing.** If you want the
check to be able to speak for an item, give the work a branch of its own.

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
