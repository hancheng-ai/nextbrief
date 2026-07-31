# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **The zero-dependency guard now runs on the interpreter it exists for.** The
  test that asserts `webform.py` imports nothing outside the standard library
  used `sys.stdlib_module_names`, which arrived in 3.10. On the 3.9 floor it
  raised before reaching its assertion, so CI was red for two commits while a
  local run on a newer interpreter reported green.

  It now asks the import system where a module actually lives, which works on
  every supported version and is the better question besides: a name list says
  `json` is standard library, but cannot say whether *this* interpreter's `json`
  came from there or from something earlier on `sys.path`. The negative case has
  its own test, because a guard that has only ever been seen to pass is
  indistinguishable from one that cannot fail.

- **An age is no longer allowed to be negative.** The recency contest picks the
  smallest age, so a file dated in the future won it outright and carried a
  negative `days_since` into the scorer — where the decay term `0.5 ** (days /
  half_life)` is bounded above zero and unbounded below it.

  A file dated a year ahead scored 477911 against a normal maximum of 4. Far
  enough ahead it stopped being a wrong number and became an `OverflowError`
  raised from inside a sort key, which costs the whole brief rather than one row.
  A machine clock ahead of this one, or an archive unpacked with its original
  timestamps, is enough to do it.

  Floored in `sense`, where it is also recorded rather than silently corrected,
  and floored again in `render`, which re-reads an existing snapshot without
  re-sensing. The bound is now a property test over every shape `days_since` can
  hold.

- **A hand-written `lead_days` or `neglect_days` no longer takes the run down.**
  `registry.jsonc` invites hand-editing and `check_shapes` validates containers
  rather than leaves, so `"lead_days": "21"` reached a comparison and raised
  `TypeError` out of the sense stage — no brief at all, for every other project
  as well as the one with the typo.

  `"lead_days": null` did the same, and is the likeliest way in: a
  `.get(field, 21)` returns its default only when the key is *absent*, so writing
  it out explicitly, which reads like "no window here", broke the run.

### Changed

- **The config template now contains only keys the engine reads.** `init` writes
  that file into the workspace, so every key in it is a promise that changing the
  number changes something. Nine were read by nothing: `renotify_days` in two
  sections, `recheck_days`, `recheck_budget_per_run`, `tz_offset_hours`,
  `exclude_when_blocked_by_decision`, `cost.alert_usd_7d`,
  `external_tools.ccusage`, and `notify.sink`.

  That last one was worse than dead. The sink layer reads `notify.backend`, so
  setting `sink` to `"none"` to stop notifications left them switched on — a
  setting that silently did the opposite of what its owner asked, with nothing in
  the system to contradict the file. `notify.only_if` likewise listed a
  `decision_pending_new` reason `should_notify` never tests, and a reason it does
  not know is simply never true: silence where you asked for noise.

  Two tests keep it that way, both parsing rather than grepping — `ccusage`
  appeared in a comment while being read by nothing, and a grep would have called
  that a reference.

  The behaviour those keys described is unchanged where it existed: a project
  blocked on a decision still goes under "decisions pending" rather than
  "neglected", as behaviour rather than as a setting.

- **`scoring.tier_weight` is reported as retired rather than silently ignored.**
  It sat in the shipped defaults under a comment promising it was read whenever
  `status_weight` was absent. No line of code kept that promise, and none could:
  `scoring_of` merges the defaults first, so `status_weight` is never absent.

  Nor can the promise be honoured now. The old table weighed `flagship` 1.3 and
  `active` 1.0 and both migrate to the single status `active`, so there is no
  weight a translation could pick — which is the ambiguity that split the field.
  A config still naming it is recorded in `parse_failed` with the rename to make.

### Added

- **A `leak-shapes` CI job, which is a report and not a fence.** The fence stays
  `.githooks/pre-push`, which runs before anything leaves the machine. This runs
  after, so it can only ever report.

  It covers the one case the hook cannot: the hook needs activating once per
  clone, so a contributor who never ran that line has no check at all. Pass 1 is
  the only pass a runner can do — the other two read files that exist on one
  machine — and it names nobody, so its output is safe in a public log. The new
  `--shapes-only` flag makes the job say which passes it skipped instead of
  printing two "did not run" notices that read like breakage.

  It is scoped to what a change adds rather than to the whole history, so it
  cannot go red for something already public and unfixable.

- **`tier` is gone from everything except the migration that reads it.** The
  shipped registry template, the config template, the example workspace, the
  `projects` table, `ARCHITECTURE.md` and every docstring that argued from it now
  say `status` and `positioning`. `status_weight` replaces `tier_weight`, with
  `frozen` and `done` where `dormant` and `flagship` were.

  A registry that still declares `tier` keeps working — it migrates on read —
  but nothing in the package teaches it any more. The remedy the brief prints
  when a project stalls now names a field `review` can actually write, which the
  old one did not.

  Note `automation.tier` on a backlog item (`hook`/`skill`/`explore`) is a
  different field and is untouched.

### Added

- **Two ways to answer `review` besides the terminal.**

  `nextbrief review` now opens the whole review as one file in `$EDITOR` — every
  project, every question, whatever is already known left visible while the rest
  is filled in. Save and close to record, like `git commit`.

  `nextbrief review --web` serves the same review as a browser form instead.
  This costs a socket, not a dependency: `webbrowser` already opens `BRIEF.html`
  and `http.server` is standard library. It binds 127.0.0.1 on a port the OS
  picks, the URL carries a one-shot token so another tab cannot post to it by
  guessing, and it stops after one answer. A test asserts the module imports
  nothing outside the standard library, because this is the file most likely to
  tempt someone away from the zero-dependency rule.

  `nextbrief review --prompt` keeps the original question-at-a-time loop, and it
  is still what runs where there is no `$EDITOR`.

  A prompt loop was the wrong shape for four heterogeneous questions across a
  dozen projects: fixed order, one project visible at a time, no way back, and a
  free-text date made as awkward as a menu.

  All three go through one coercion function. Two validators would eventually
  disagree, and the disagreement would show up as an answer that records from one
  input and vanishes from another. A hand-edited line that will not parse costs
  that line and nothing else — the file is edited by a person, and one mistyped
  date should not discard eleven other projects' answers.

### Added

- **`review` asks four questions instead of one**, and no two of them are the
  same question in other words:

  | | asks | lands in |
  |---|---|---|
  | impact | what changes if this succeeds | `ice.impact` — the score base |
  | positioning | what it is meant to *become* | `positioning` |
  | status | what phase it is in | `status` — which verdicts may fire |
  | urgency | a date, if one matters | `deadlines` |

  They are separable by counter-example, which is the test for whether a question
  earns its place: a project can be small today and be the thing everything else
  is planned around (impact vs positioning), busy and finished evolving (activity
  vs status), and important with no date at all (impact vs urgency).

  Urgency is asked as a **date, not a rating**. A stored "urgency: 4" is wrong
  within a week; a date stays true and recomputes its own urgency every morning.
  It is validated on entry, because a deadline that never parses is a deadline
  that silently never fires.

  `ASKED_VERSION` moves to 3, so answers given to the one-question version are
  asked again rather than reinterpreted.

- **`init` no longer guesses a phase from file age.** It used to write
  `active`/`maintenance`/`dormant` based on how recently a directory had been
  touched, which is the conflation `status` exists to end: age tells you whether
  something is *hot*, not whether its owner considers it finished. A busy project
  can be one that has stopped evolving. `init` now writes no phase and `review`
  asks.

### Added

- **`status`** — a project's phase: `active`, `maintenance`, `frozen`, `done`.

  `tier` said two things at once and could answer only one of them at a time.
  `flagship` was a claim about a project's place in the portfolio; `dormant` was
  a claim about its phase. Both can be true of one project — a flagship that is
  frozen is an ordinary thing to own — and the single field made you choose which
  to record.

  Phase is what the engine reasons with: which verdicts may fire, and how much
  the score is damped. The portfolio claim moves to `positioning`, which is prose
  for a reader rather than an input to arithmetic.

  Only an **active** project can be reported *neglected* or *stalled*.
  `maintenance` is the declaration that a project is meant to be quiet, and
  warning about something doing exactly what was asked of it is how a warnings
  column stops being read. `done` weighs 0 and leaves the ranking rather than
  lingering at the bottom of it.

  This also keeps *hot* and *phase* apart, which `tier` could not. Activity is
  observed from commits; phase is declared. A project can be busy and finished
  evolving at the same time, and the brief can now say so.

  Old registries keep working: `tier` migrates (`flagship`/`active` → `active`,
  `maintenance` → `maintenance`, `dormant` → `frozen`), and the read goes through
  that migration everywhere — including `render`, which re-reads an existing
  `snapshot.json` without re-sensing. Without that an upgrade would have silently
  stopped producing verdicts on any workspace that had not re-sensed yet.

  An undeclared status still weighs 1.0 — `review` has not asked yet, and a
  project should not be demoted for a question nobody put to its owner. What it
  withholds is a *verdict*: saying "you have neglected this" requires knowing it
  was supposed to be moving.


### Changed

- **One scoring rule instead of two.** The base is now the declared `impact`
  alone. `confidence` and `effort` still parse from a registry and are ignored.

  They used to default to 3 so that a hand-written three-axis entry scored
  exactly as before — courtesy to existing files that produced two rules in one
  ordered list. A project rated 5 and divided by effort 5 ranked below one rated
  4 and divided by 2: the penalty-for-being-large that `annotate.py` cites as the
  reason effort stopped being asked, still operating one layer below where it was
  removed. An ordering with two definitions is not an ordering.

- **`review` restates answers that have gone stale.** Each answer is stamped with
  the date it was given, and one older than `RESTATE_AFTER_DAYS` (180) is asked
  again. `review --all` restates everything.

  Importance drifts and the engine cannot observe that. The alternative — a
  command for editing an answer — assumes you remember a number you set half a
  year ago and think to revisit it. An answer with no stamp is treated as unknown
  age rather than fresh, which is the same rule the rest of the engine applies to
  every other absence.

### Fixed

- **A malformed `impact` no longer takes the brief down with it.** `registry.jsonc`
  invites hand-editing and `check_shapes` only validates that `ice` is a dict, so
  `"impact": "high"` reached the scorer intact. It now reads as no answer at all.
  `NaN` and infinities are excluded too: `NaN` compares false against everything,
  so one of them makes the sort key non-total and quietly withdraws the
  byte-identical-output guarantee.

- **A parked project is no longer told to park itself.** A `dormant` project with
  uncommitted changes is reported — work living only in a working tree nobody
  opens is the easiest kind to lose — but it was given the generic remedy, which
  ends "or move the tier to dormant so it stops showing up". Advice to do what it
  had already done, and which would not have worked. The README shipped a worked
  example of it failing.


### Fixed

- **The renderer no longer invents the importance the sensing stage refuses to
  invent.** Discovery states `tier: null` and `ice: null` for a project nobody
  has judged, on the stated grounds that a synthesised midpoint is an assertion
  rather than a neutral guess. `score_project` then read a missing `impact` as
  `3` and a missing `tier` as `"active"` — so an unreviewed project was ranked
  on numbers nobody supplied, while `classify` twenty lines away read the same
  missing tier honestly and declined to reach a verdict. One absent field, two
  readings, one file.

  Projects with no declared importance are now **listed but not ranked**. They
  keep their row, their evidence and their deadlines — a date is a fact, not a
  judgement, so the most overdue thing you own can still be something nobody has
  rated — and the ordering claim is restricted to projects a human has actually
  judged. A reminder names them and the command that fixes it.

  Note what is *not* removed: `confidence` and `effort` keep their neutral
  default of 3. Those axes are deliberately never asked, and the 3s are what
  collapse the base to `(impact × 3) / 3 == impact`, which is what lets a
  one-question review produce a usable score. Only the invented `impact` was the
  defect.


## [0.1.0rc14] - 2026-07-30

### Added

- **`scripts/privacy-scan.py`, and a `pre-push` hook that runs it.** A push that
  would publish content copied out of a private directory is refused before the
  objects leave the machine.

  Local, and deliberately with no remote counterpart. A CI job would run after
  the push, a pull request is public from the moment it opens, and a force-push
  does not retract objects that have already landed — so there is no point in a
  remote pipeline where such a check is still preventive. Activate it once per
  clone with `git config core.hooksPath .githooks`, repo-local rather than
  global, so it governs this repository and nothing else on the machine.

  It scans commits, not the working tree, because a push publishes history and a
  clean tip says nothing about what is behind it. Three passes: credential and
  home-path shapes, built in and generic; a list of identifiers supplied out of
  band, since a denylist kept in a repository publishes exactly what it protects;
  and, where a private directory is configured, lines appearing in both — the
  only pass that can catch a borrowed example whose names have been changed,
  because there is nothing left for a name to match. Each pass announces itself
  when it cannot run: a check that silently covers less than you think is worse
  than one that is plainly absent.

- **`nextbrief describe <id> --capability "<text>"`** — what the thing built here
  could also serve, as against what it currently does. Always declared, never
  derived: a manifest says what a package is, and no file says that something
  generalises beyond its current use.

### Changed

- The identifier scan is gone from CI. It could only ever run its weakest pass
  there — the other two need a local configuration a runner does not have — and
  what it did run, it ran too late to matter.

- The `--help` command list leads with the commands rather than argparse's
  positional-arguments block.

### Fixed

- **`nextbrief context` never printed `capability`.** The field reached the
  snapshot, the inventory and `context --json`, and stopped one layer short of
  the listing a person actually reads. Now printed on its own prefixed line,
  interpolated by the locale catalogue so each language owns its own spacing.

- **`describe <id>` with no text is a usage error** rather than a silent clear.
  A forgotten argument looked identical to an intent to erase; `describe <id> ""`
  is how you clear it.

- Examples and fixtures across the prompts, one module docstring and the
  `describe` usage strings replaced with invented ones.

## [0.1.0rc13] - 2026-07-29

### Changed

- **The engine's own source checkout is a project like any other.** It used to be
  excluded from discovery on the theory that it is "the tool, not the work" —
  which is exactly backwards for anyone developing it, and bought nothing anyway.
  The engine writes only into the workspace, so its own checkout cannot feed its
  output back to itself. What needs a fence is where output *lands*, not where the
  tool happens to live.

### Fixed

- **The workspace fence tested equality, so one level of nesting walked straight
  past it.** With the workspace at `<root>/tools/pm`, the candidate discovery sees
  is `<root>/tools` — equal to nothing reserved, and containing every file the
  engine writes. Adopted, it would be re-sensed each night off the previous
  night's output and could never look stale. Containment is the property that
  matters, so containment is now what gets tested.

- **The engine's own output no longer counts as project activity.** Declaring the
  workspace as one of your own projects is supported and mildly self-referential
  on purpose — the registry template suggests it, so a brief nobody reads is
  eventually reported as neglected by itself. That only works if the run's
  products stay out of the activity it measures.

  Keeping them out used to be the reader's job, spelled out by hand in
  `ignore_globs`. A hand-kept list of somebody else's filenames goes stale in one
  direction only: a release adds an output file, every existing list still parses,
  still passes, still looks right, and quietly starts crediting the project with
  work it did not do. That had already happened — a list naming `BRIEF.md` did not
  name `BRIEF.html`, so each night's render came back the next night as a day of
  activity. `state/`, `log/`, `BRIEF.md` and `BRIEF.html` are now derived from
  where the workspace actually writes, wherever `out` points.

## [0.1.0rc12] - 2026-07-29

### Added

- **`capability`** — what the thing built here could also serve, beyond what it
  currently does. A registry field, a `describe --capability` flag, and a second
  labelled block in the inventory.

  A description says what a project *is*, and one sentence covers that. It does
  not cover the question an agent weighing "build this, or reuse something?"
  actually has, which is what capability was built and where else it applies. A
  tool built for one customer is also a rules engine whose customer is a
  configuration; a single report is also a pipeline that could produce a hundred
  more. Collapse those into "what it is" and the reuse question becomes
  unanswerable from the artifact.

  **Always declared, never derived, and with no fallback.** A manifest states what
  a package is; no file on disk states that something generalises beyond its
  current use. That is a judgement about potential — the most speculative thing
  here — so it carries its own `declared` label and an agent can see it is reading
  somebody's optimism rather than a fact about the tree.

- **`nextbrief describe <id> "<sentence>"`** — say what a project is without
  opening a JSON file.

  Descriptions had no path in. `review` captures answers to fixed questions, but
  a description is free text and cannot be multiple choice, so the only way to
  supply one was to hand-edit `registry.jsonc` — exactly the friction the overlay
  exists to remove. Recorded in `annotations.jsonc`, never the registry, and a
  `description` typed into the registry by hand still wins.

  An id that is not a project is refused rather than recorded: a description
  nothing will ever read is worse than none.

- **`state/inventory.json` and `nextbrief context`** — what each project *is*,
  as opposed to what it did this week.

  `digest.json` is an activity report: what moved, how fresh, what is due. That
  is what the brief needs and it never says what a project is *for*. An agent
  asked "should we build X?" needs the other question answered, and re-deriving
  it by walking the tree is what every agent otherwise does separately, every
  session. A separate artifact rather than a heavier digest, because the two have
  different consumers and different cadence — activity changes daily, capability
  monthly — and because it has to stay cheap: 4.7 KB against the digest's 31 KB
  on a twelve-project portfolio.

  **Derived where it can be, declared where it cannot, never blended.** A
  project's own `package.json`, `pyproject.toml` or plugin manifest already
  states what it is, and a README's first prose line usually does too. Those are
  observations and each carries the file it came from, so a reader can check it.
  Where nothing exists — roughly the content projects on a real portfolio —
  nothing is invented: the entry says so, which is the one thing a person can fix
  in ten seconds.

  That labelling is the safety property, not a nicety. A reader must be able to
  tell "orchard is a tenancy API", which its manifest says checkably, from
  "orchard is our flagship", which is a thing a person typed. `context --json`
  prints the file verbatim for another tool to consume.

- **`registry.projects[].description`** — one sentence saying what a project is,
  for when there is no manifest to read or the manifest is wrong. Distinct from
  `goal_one_line`, which is what you intend to do about it next.

### Changed

- `describe <id>` with no text is now a usage error rather than a silent clear. A
  forgotten argument looked identical to an intent to erase; `describe <id> ""`
  is how you clear it. And `--capability` no longer blanks a description it did
  not mention.

### Fixed

- **A reworded question destroyed descriptions along with answers.** The version
  check dropped the whole overlay entry, and a description was never an answer to
  a worded question. It is now scoped to `ice` and only `ice`.

- **A description in the overlay never reached the inventory.**
  `apply_annotations` rebinds `reg` inside `build()`, so `main()` still held the
  unmerged registry and passed *that* to the inventory. The description is now
  carried into the snapshot alongside `goal_one_line`, which it resembles, so the
  inventory reads one post-overlay source instead of re-deriving the merge in a
  scope that never saw it.

- **Withdrawn answers survived inside the snapshot.** `load_annotations` drops
  answers given to a reworded question, but the snapshot is written with them
  already merged in — so a workspace that does not re-sense kept scoring on
  answers that had been retired, indefinitely. The snapshot now records which
  projects got their `ice` from an answer, and stamps the wording version; a
  stale stamp discounts those and only those. A value its owner typed into the
  registry is never retired by us rewording a question.

## [0.1.0rc11] - 2026-07-29


### Added

- **`needs`** — a project may declare that it is waiting on other projects, and
  the inverse `unlocks` edge is computed for free. This is the relationship the
  registry could not express: `blocked_by` names a *kind* of blocker, and
  `external_dependency` names someone outside, but neither could say "this waits
  on that work existing".

  The reverse edge is the useful half. A contributor now knows what finishing it
  would release — machine-readable, so an agent choosing what to work on can see
  that one project sits under three others rather than having to infer it.

  It is a graph, and being a graph earns three things in about thirty lines of
  standard library, with no dependency and no index: a dangling id is reported
  rather than dropped, a **cycle** is reported because it can never resolve, and
  the transitive closure (`needs_all`) answers what a project *ultimately* waits
  on. Cycle detection is iterative, so a deep hand-written chain cannot exhaust
  the interpreter's stack.

  Nothing decides when a need is **met**. That belongs to whoever wrote the
  declaration; a rule like "met once the other project is hot" would be the
  engine inventing a judgement, which is what the rest of it exists to refuse.

- A project with unmet `needs` is classified as **waiting on other work**, not
  neglected or stalled — for the case `blocked_by: decision` does not already
  cover, which is waiting on *work* rather than on a judgement of your own.

- **`nextbrief projects`** — one line per project, straight from the snapshot:
  signal, days since evidence, what that evidence was, tier, and a marker for
  anything discovery adopted that the registry never named.

  `ls` lists backlog items and nothing listed projects, so the only way to see
  the portfolio was to render a whole brief and read the table inside it. That
  was tolerable while the registry *was* the project list. It stopped being
  tolerable when discovery began adopting directories on its own: the set can now
  change without anyone editing anything, and "what is the tool actually
  watching?" had no cheap answer. No model, no render, no writes.

### Changed

- **Licence: Apache 2.0**, for its explicit patent grant and because that is
  what corporate legal review is used to reading. Named the same way in
  `LICENSE`, `pyproject.toml`, `CITATION.cff` and the Homebrew formula.

  The engine stays permissively licensed on purpose. Its value is that the
  evidence gate can be audited, and a gate nobody can read is worth nothing;
  anything commercial belongs in modules that *consume* gated output, not in
  restrictions on the engine that produces it.

- **`review` asks about importance, and stops asking about urgency.** The first
  version asked *"if this slipped by a month, what happens?"* — a
  delay-consequence question, which is urgency wearing importance's name. It
  scored a portfolio's centre piece at 1 of 5, because nothing happens when a
  platform blocked on its own ecosystem slips another month. Everything
  important-but-not-urgent was systematically undervalued, which is the category
  a long-horizon plan is made of.

  It now asks *"if this succeeded completely, what changes?"* — answered the same
  way whether a project was touched today or last spring. Urgency is not asked at
  all, because it is already known: it comes from the dates in `outcomes` and
  `deadlines`, which the renderer already turns into a boost.

- **Confidence and effort are no longer asked or derived.** The confidence
  question measured how clearly you knew the next step — actionability, not
  confidence — and multiplied a low-importance project by five for being
  well-understood. Effort was derived from file count, which is repo *size*, not
  the work needed to reach the impact; it penalised a flagship for being large
  and rewarded a finished tool for being small. Together they ranked a mature
  10-file utility above a flagship with a hard external deadline.

  Nothing in `score_project` changed: with both defaulting to 3, the base
  collapses to `(impact × 3) / 3 == impact`, so a one-question answer scores as
  itself and hand-written three-axis registry entries keep working untouched.

- **`--help` prints one command list instead of two.** argparse generated its own
  list of every subcommand and printed it above the hand-written one — the same
  twenty commands twice, in two orders and two levels of detail. The written list
  wins: it groups by what you are trying to do, which the alphabetical machine
  version cannot. It also moved from the epilog into the description, so the
  reader meets the commands before the flags.

### Fixed

- **`review` re-asked what you had just answered.** The overlay is applied at
  sense time, so a snapshot written before the last `review` still showed no
  answers — and `review` read that snapshot to decide what to ask. It now merges
  the overlay first.

- **A reworded question invalidates its old answers.** `annotations.jsonc` carries
  `asked_version`; answers recorded under an earlier wording are dropped and asked
  again rather than silently reinterpreted. "2" against *what breaks if this
  slips* is not the same statement as "2" against *what changes if this succeeds*.

## [0.1.0rc10] - 2026-07-29

### Fixed

- **The question section evicted the brief's warnings.** Gate 4 keeps the first
  `brief_max_lines` and drops the tail, and `0.1.0rc9` placed the questions above
  the reminders — so on a full brief the reminders and the provenance footer fell
  off the page. Measured on a real workspace: 58 lines and 0 truncated before,
  59 and 15 after, with both sections gone. The lost lines are the brief's only
  warnings: which projects have no version control and are unrecoverable if
  deleted, which status documents contradict each other. Worse, the nightly could
  not answer the question that displaced them — `review` refuses a non-TTY — so it
  recurred every night at the same cost.

  The questions now render last. A question that waits a night costs nothing; a
  warning that disappears is the failure this engine exists to prevent.

- **`BRIEF.md` is line-capped and `BRIEF.html` is not**, so the two diverge the
  moment the cap bites. The HTML now says so, naming how many lines only it
  carries. Neither rendering decides anything the other does not, but silence
  about the difference is its own kind of disagreement.

- **Discovery silently dropped directories whose names collapse to one id.**
  `My App`, `my-app`, `my.app` and `my_app` in one root yielded two projects; the
  other two vanished with no error, no `parse_failed` entry and no count — which
  is verbatim the failure this module was written to eliminate, reproduced inside
  it. Colliding ids are now numbered rather than discarded.

- **`docs/ARCHITECTURE.md` described behaviour `0.1.0rc9` had removed** — that a
  discovered project carries "neutral placeholders" — and contradicted itself a
  hundred lines later. It shipped that way because the file was the one document
  `test_docs_consistency` never opened. Corrected, and now covered: the test
  checks every `nextbrief` command it names, every registry key it documents, and
  ties the placeholder claim to `DISCOVERED_TIER` so the assertion retires itself
  if a default tier is ever deliberately reintroduced.

- **A registry path written as `./x` claimed nothing.** `claimed_segments`
  stripped slashes and split, which turns `"./handoff-inbox"` into a segment of
  `"."`. Consequences, both silent: an `ignored` entry stopped ignoring — and
  `ignored` is the only opt-out for `defaults.root`, so the directory was walked
  and its filenames reached the snapshot and then the digest the model reads; and
  a declared project was adopted a *second* time as an undeclared duplicate with
  its files counted twice, which is the precise outcome that function exists to
  prevent. The `./` form is not contrived — this repo's own example workspace
  uses it for `defaults.root`. Backslashes are normalised too.

- **A hand-edited `annotations.jsonc` could kill the run.** `apply_annotations`
  did `dict(value or {})` on the overlay's `ice`, so `"ice": "high"` raised
  straight out of `build` — a stack trace and no brief, on the unattended path,
  contradicting the fail-open contract `load_annotations` states one function
  above. `check_shapes` never sees the overlay, and the file's own header invites
  editing it, so the shapes are now checked rather than trusted.

- **The question section reached `BRIEF.md` but not `BRIEF.html`**, so the
  rendering meant for reading asked nothing. Both artifacts render from one gated
  dataset and neither decides anything for itself; this one disagreed on arrival.
  A test now asserts they ask the same question and both drop it once answered.

## [0.1.0rc9] - 2026-07-28

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
  `atlas`); anything in `ignored`; dotfile directories and build/OS folders; the
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

[Unreleased]: https://github.com/hancheng-ai/nextbrief/compare/v0.1.0rc14...HEAD
[0.1.0rc14]: https://github.com/hancheng-ai/nextbrief/releases/tag/v0.1.0rc14
[0.1.0rc13]: https://github.com/hancheng-ai/nextbrief/releases/tag/v0.1.0rc13
[0.1.0rc12]: https://github.com/hancheng-ai/nextbrief/releases/tag/v0.1.0rc12
[0.1.0rc11]: https://github.com/hancheng-ai/nextbrief/releases/tag/v0.1.0rc11
[0.1.0rc10]: https://github.com/hancheng-ai/nextbrief/releases/tag/v0.1.0rc10
[0.1.0rc9]: https://github.com/hancheng-ai/nextbrief/releases/tag/v0.1.0rc9
[0.1.0rc8]: https://github.com/hancheng-ai/nextbrief/releases/tag/v0.1.0rc8
[0.1.0rc7]: https://github.com/hancheng-ai/nextbrief/releases/tag/v0.1.0rc7
[0.1.0rc6]: https://github.com/hancheng-ai/nextbrief/releases/tag/v0.1.0rc6
[0.1.0rc5]: https://github.com/hancheng-ai/nextbrief/releases/tag/v0.1.0rc5
