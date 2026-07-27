---
id: NA-0002
title: Publish the March essay once the draft arrives
project: lantern-site
type: task
status: waiting
priority: 2
blocked_by: external-party
is_next_action: false
automation:
  tier: skill
  what_agent_can_do: Run the publish checklist in PUBLISHING.md against a draft -- front matter, slug, image widths, internal links, feed rebuild -- and report what fails.
  what_needs_human: Writing the essay. Permanently. Automating the checklist is worthwhile; automating the writing would defeat the purpose of the site.
  next_probe: null
  assessed_on: 2026-03-16
  human_confirmed: true
source:
  doc: atelier/sites/lantern/PUBLISHING.md
  anchor: "Cadence: one essay a month"
  seen_on: 2026-03-16
  source_last_updated_declared: 2026-03-09
estimate_min: 20
dependencies: []
discovered_from: NA-0001
created_date: 2026-03-04
updated_date: 2026-03-16
created_by: human
human_confirmed: true
---

<!-- SECTION:NEXT_ACTION:BEGIN -->
Nothing to do until the draft exists. When it lands, run the publish checklist and ship it.
<!-- SECTION:NEXT_ACTION:END -->

<!-- AC:BEGIN -->
- [ ] #1 Draft received
- [ ] #2 Publish checklist passes with no manual fixes
- [ ] #3 Post is live and appears in the feed
<!-- AC:END -->

<!-- SECTION:NOTES:BEGIN -->
This item exists so the wait is visible, not so it can be chased. It sits under
"waiting for" and produces no next action; an item blocked on another person that
keeps surfacing as work to do is how a brief teaches you to skim past it.

`human_confirmed: true` freezes the automation block. The judgement that the writing
itself must stay human was made once, deliberately, and should not be re-derived by a
model that notices the checklist is scriptable.
<!-- SECTION:NOTES:END -->
