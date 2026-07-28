# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **`nextbrief review`, and a question channel in the brief.** The registry
  wanted three integers per project and nobody supplied them — not from laziness,
  but because the question is harder than the judgement it captures. "Impact 4"
  is an absolute number on a scale nobody defined, unanswerable in the moment and
  unreadable a month later.

  So nothing asks for a number any more. **Effort is never asked** — it is
  measured from what is on disk, the one axis where a guess is worse than a
  count. **Impact and confidence are asked as consequences**, multiple choice:
  *"If this slipped by a month, what happens?"* — nothing, I'd be annoyed, a date
  slips or someone is blocked, I'd drop other things to protect it. Answerable in
  a second, and comparable between projects and across time in a way a remembered
  "4" is not.

  The brief carries at most `caps.max_questions` (default 2) of these, most
  recently active first, and each disappears the moment it is answered. So the
  backlog of unanswered projects drains over a fortnight without anyone
  scheduling a setup session.

- **`annotations.jsonc`** — where those answers land. Never `registry.jsonc`:
  that file is the human's, comments and ordering included, and a tool that
  rewrites it will eventually get that wrong on the file whose loss costs most.
  Anything typed into the registry overrides the overlay, so a hand edit is never
  quietly undone. Applied *after* discovery, so a discovered project can be
  annotated without first being declared — which is the point, since the person
  who has not written a registry entry is exactly the person being asked.

  `review` refuses to prompt when stdin is not a terminal, and says what it would
  have asked instead. A scheduled run that blocks on a prompt at 21:30 produces
  nothing at all, and this command is named in the brief.


## [0.1.0rc8] - 2026-07-28

### Added

- **Outcomes** — `registry.outcomes` plus per-project `serves`. A deadline is a
  property of a commitment, not of a directory; written into three projects it
  becomes three deadlines, each boosting its own project, so one commitment
  produces three urgent rows and all three mint the same colliding
  `deadline:<date>` citation handle. Declared once as an outcome, contributors
  inherit its urgency through `max` — lifted exactly as much as an own deadline
  would have, no more and no less — and cite one handle, `outcome:<id>`.

  Two kinds. `dated` carries urgency, because a date is a fact and days-until is
  arithmetic over it. `compounding` carries **none**: there is no date to be near,
  and a constant meaning "long-term work counts extra" would be a number nothing
  can cite. The evidence gate could not catch it either — a sort weight never
  appears on the page as a claim, so nothing asks it for a source. A compounding
  outcome groups contributors and tells stage 2 they serve one aim; ranking still
  comes from `tier` and `ice`, which a human wrote.

  Outcomes reach the digest, so stage 2 can see them — a ranking signal the model
  never receives changes nothing the model writes. A `serves` id naming no declared
  outcome is recorded in `parse_failed` rather than dropped: silently ignoring it
  leaves the project looking unattached, which is indistinguishable from never
  having declared the link.

- **`outcomes[].done`** — a met commitment stops shouting. A dated outcome whose
  date has passed is `overdue`, which takes the maximum urgency boost: right for
  one you missed, permanent nonsense for one you met, and the contributors stay
  pinned to the top of the table for something that is finished. The engine cannot
  tell those apart — both are a date in the past, and the difference is entirely a
  fact about what happened. `done: true` is the person who was there saying which.

## [0.1.0rc7] - 2026-07-28

### Added

- **Projects are discovered, not declared.** Everything in `defaults.root` is
  sensed, whether or not the registry names it. A directory you add tomorrow is
  in tomorrow's brief with no edit to `registry.jsonc`.

  The registry stops being the list of what exists and becomes the list of what
  you have said something *about* — a tier, a goal, a deadline, a privacy rule.
  A discovered project is ranked like any other but carries neutral placeholders
  instead of judgements, and the snapshot marks it `declared: false`. No
  `goal_one_line` is invented: that is the one field where a placeholder would
  be a fabrication rather than an absence.

  This replaces a failure with no symptom. Under declare-first, a project you
  started and never registered was not reported as missing — it was absent, and
  the brief read exactly like a brief for a week in which nothing else happened.

  Never adopted: anything already in `projects` / `watch` / `infra` / `archived`
  (matched on the first path segment, so `atlas/apps/site` still claims
  `novel`); anything in `ignored`; dotfile directories and build/OS folders; the
  workspace itself; and nextbrief's own source checkout.

### Fixed

- **`ignored` now does something during a run.** It was read only by `init`, so
  a list written specifically to keep directories out of the brief had no effect
  on any brief. It is the opt-out for discovery, and the sensing stage honours
  it.

## [0.1.0rc6] - 2026-07-28

Second prerelease, still on TestPyPI. The engine's read-only promise stops being
a property of the code that remembered it and becomes a property of the package.

### Added

- **Containment and delete gates**, in a new `nextbrief.fs` module that is now
  the only place in the package that mutates a filesystem. Every write, append,
  frontmatter rewrite, rename and delete is checked against the resolved
  workspace before it happens. Previously this held only for the two helpers in
  the renderer; the sensing stage, the CLI and the frontmatter writer wrote
  unchecked, so the guarantee covered roughly the code that remembered it.
- **Human-only paths.** Backlog entries, `registry.jsonc` and `config.jsonc`
  cannot be deleted or renamed by the engine. The write-permission gate already
  refused to let anything automated set a terminal status; deleting an entry
  reaches the same end state and destroys the record that it ever existed, so it
  is refused in the same spirit. Directories are refused outright — there is no
  recursive delete and nothing has needed one.
- **Declared exits.** The three places that legitimately write outside a
  workspace — the `init` pointer file, `permissions --merge-into`, and its
  backup — now name themselves against a list in `fs.ESCAPES`. An undeclared
  reason raises. A test asserts the list and the call sites have not drifted
  apart, and that neither `sense` nor `render` can reach the door at all.
- `nextbrief done` now refuses to `git add` a path outside the workspace, so a
  commit cannot stage someone else's work under a backlog message.

### Changed

- `append_jsonl` and `append_text` now **raise** on a target outside the
  workspace instead of returning `False`. Log writes remain fail-open for
  `OSError` — a full disk still costs a log line rather than the run — but an
  out-of-workspace target is a caller bug, and a bug that returns `False` ships.
  No caller in the package was affected; all of them append inside `log/`.

## [0.1.0rc5] - 2026-07-27

First public prerelease. On [TestPyPI](https://test.pypi.org/project/nextbrief/),
not PyPI, and the GitHub release is marked as a prerelease — the version routes
itself there, because publishing an rc to the real index cannot be undone.

The engine had been running nightly against a private multi-project workspace for
some months before being extracted into a package; this is that code, with the
workspace removed and the interfaces made configurable. `0.1.0` will follow once
the rc has been used by someone who did not write it.

### Added

- **Three-stage pipeline.** `sense` walks the projects you own and writes a
  deterministic `state/snapshot.json`; a model reads a compact digest of it and
  writes `state/brief.json`; `render` turns that into `BRIEF.md` and
  `BRIEF.html`. Only the middle stage involves a model.
- **Evidence gate.** Every claim the model makes must cite a source that
  resolves against the snapshot. Claims that do not resolve are never rendered —
  they go to `log/rejected.jsonl` with a reason code. The check lives in the
  renderer rather than the prompt, so it cannot be drifted from.
- **Non-goals gate.** Things a project has declared it will not do are lifted
  verbatim into the snapshot; proposals that collide with one are flagged rather
  than removed.
- **Write-permission gate.** Backlog items are diffed field-by-field against
  their committed versions and out-of-bounds edits are reverted. No agent can
  set a terminal status; it may only propose one for a human to confirm.
- **Caps gate.** Per-section limits with overflow written to
  `log/deferred.jsonl`, so the brief cannot grow without bound and nothing is
  lost when it hits the limit.
- **Two renderings, one dataset.** `BRIEF.md` for terminals and diffs,
  `BRIEF.html` for reading — expandable items, copy-to-clipboard commands,
  light/dark, fully offline. The HTML re-decides nothing, so the two cannot
  disagree.
- **`launch` context builder.** Proposes candidate working directories for a
  backlog item, ordered by how likely each is to be the right one, and opens a
  session pre-loaded with context. No input is treated as cancel.
- **Pluggable providers and sinks.** The model backend and the notification
  channel are both swappable modules with narrow contracts.
- **English and Simplified Chinese locales**, with all user-facing strings in
  catalogs rather than in code.
- **JSONC configuration.** `registry.jsonc` declares what your projects are and
  which documents to read; `config.jsonc` holds thresholds and weights. Comments
  and trailing commas are permitted, because configuration you cannot annotate
  is configuration nobody maintains.
- **Idempotence self-check** that exits `3` when a re-run would change the
  snapshot, so the nightly job is safe to trigger at any time.
- **Cost controls.** A compact digest is sent to the model instead of the full
  snapshot; on the reference workspace this cut per-run cost from $4.37 to
  $0.74, mostly by reducing agent turns rather than bytes. See
  [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

### Design constraints

Not features, but the reasons the code looks the way it does:

- **Zero runtime dependencies**, standard library only, Python 3.9 and up. The
  nightly run is started by a GUI scheduler with a minimal `PATH` and must work
  under the system interpreter.
- **Read-only outside the workspace.** The engine reads your projects and writes
  only its own directory, so it can never damage anything it observes.
- **Fail-open throughout.** A parser that cannot understand a file records the
  path and returns nothing; external tools are optional. One bad document does
  not cost you the brief.

[Unreleased]: https://github.com/hancheng-ai/nextbrief/compare/v0.1.0rc8...HEAD
[0.1.0rc8]: https://github.com/hancheng-ai/nextbrief/releases/tag/v0.1.0rc8
[0.1.0rc7]: https://github.com/hancheng-ai/nextbrief/releases/tag/v0.1.0rc7
[0.1.0rc6]: https://github.com/hancheng-ai/nextbrief/releases/tag/v0.1.0rc6
[0.1.0rc5]: https://github.com/hancheng-ai/nextbrief/releases/tag/v0.1.0rc5
