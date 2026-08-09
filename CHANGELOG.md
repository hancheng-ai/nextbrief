# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **A release candidate's notes name the index a candidate actually goes to.**
  The GitHub Release body opened with `uv tool install nextbrief==<version>` and
  `pipx install nextbrief==<version>`, both annotated `# from PyPI`, on every
  tag. The workflow does not publish every tag to PyPI: a version carrying a
  pre-release segment routes to TestPyPI and only a final version routes to
  PyPI, decided from the version string itself so that nobody can tick the wrong
  box. So on a candidate the first thing the notes said was a command that
  returns "no matching distribution" — measured, not inferred:
  `nextbrief==0.2.0rc4` does not resolve against PyPI, and does resolve against
  TestPyPI with the index named.

  This is the second time the same sentence has cost something. The 0.2.0 entry
  below records the Claude Code plugin shipping a week early with `pipx install
  nextbrief` in it, for exactly this reason, and the fix then was to make both
  READMEs *state which index a version routes to* rather than which one today's
  happens to be on. The release notes were not part of that sweep and kept
  saying which one today's happened to be on. They now state the rule too, in
  the same words, and a candidate's two install lines carry the index URL and
  the pinned version that a prerelease needs — TestPyPI is on nobody's default
  path and no resolver picks a prerelease unasked. The caveat printed when
  publishing is switched off moved with them, because with publishing off a
  candidate is missing from TestPyPI rather than from PyPI.

  The guard runs the release step rather than reading it. What a reader gets is
  the output of a heredoc, a six-expression `sed` and two shell branches, and a
  regex over the YAML would agree with all three being wrong; the step's script
  is extracted from `release.yml` and executed with `gh` and `sha256sum` stubbed,
  on both routing paths. What is pinned is not the wording but the wiring: the
  notes and the two publish jobs must read the same `build.outputs.prerelease`,
  which is what stops them drifting apart the next time routing changes. Making
  the step runnable off the runner also cost it its one GNU-ism — `sed -i -e`,
  which BSD sed reads as a backup suffix.

- **The check that workspace artifacts are ignored can no longer be answered by
  the machine it runs on.** `nextbrief init` scaffolds `prompts/`, `schema/`,
  `state/`, `log/`, `backlog/`, `config.jsonc`, `registry.jsonc` and
  `.claude/settings.json`; `state/snapshot.json` carries a filename from every
  project the registry tracks, including the ones marked `never_read` precisely
  because their filenames are the sensitive part. The tracked `.gitignore`
  covers all of it, and that has been verified — this is about the verification.

  `.claude/` was once held back only by a global excludes file and
  `.git/info/exclude`, neither of which travels with a clone, which is why it is
  now a tracked rule with a comment saying so. The guard written alongside that
  fix copied the tracked `.gitignore` into a fresh repository to measure coverage
  "as a clone receives it" — and the fresh repository was not isolated.
  `GIT_CONFIG_NOSYSTEM` suppresses `/etc/gitconfig`, `GIT_CONFIG_GLOBAL`
  suppresses `~/.gitconfig`, and **neither reaches the default excludes file**:
  with `core.excludesFile` unset, git falls back to `~/.config/git/ignore` by
  path rather than by configuration, so no environment variable turns it off.
  That file exists on the machine this was written on and carries a `.claude`
  rule. The substitution the fix was about could have recurred inside the test
  for it without anything changing colour.

  `core.excludesFile` is now pinned to `/dev/null` for every check, the box's
  `.git/info/exclude` is emptied rather than trusted — it is the one ignore
  source with no off switch — and two new tests hold the line from both sides: a
  rule planted where only this machine would have it must count for nothing, and
  the shipped file must still be read, because a checker that had stopped seeing
  anything at all would pass the first one perfectly.

- **A stale mutation anchor stops being invisible.** `scripts/watch-red.py`
  refuses to run a mutation whose `old` no longer appears exactly once in its
  file — correctly, since a mutation that cannot be applied proves nothing — and
  it stops the whole run there. Absolutising the README links for PyPI, one
  release ago, rewrote `](PRIVACY.md)` in `README.zh.md` to a tag-pinned URL, and
  the manifest entry aimed at that line stopped resolving. It was entry 43 of 69.
  The 26 after it had not been watched since, `watch-red` exited 2 every time it
  was run, and the suite was green throughout because **nothing in CI runs
  watch-red**.

  Which is the shape `tests/test_gate_selfcheck.py` already names one level up: a
  gate that was never installed and a gate that passed produce the same log,
  nothing. The manifest is now checked by three ordinary tests — every anchor
  resolves exactly once, no entry's `new` equals its `old`, every entry carries
  the fields the runner needs — so the next rewrite three files away fails in CI
  at the commit that causes it. The anchor itself is repaired, and carries a note
  saying it holds a release tag that `bump-version.sh` does not sweep, so the
  guard will call for it again at the next bump. That is the intended behaviour
  rather than a defect: it fails loudly, in the right place, naming the line.

- **The rules covering a rendered brief are exercised rather than assumed.** The
  coverage check walked the tree `nextbrief init --no-scan` leaves, and `BRIEF.md`,
  `BRIEF.html`, `state/snapshot.json`, `state/digest.json` and the rest are
  written at the end of a *run*, not by `init` — so several `.gitignore` rules
  had nothing exercising them. The paths are now enumerated from the `Workspace`
  properties in `paths.py`, which also means a property added later is covered
  without anyone remembering to come back.

- **Every link in both READMEs works on PyPI, not only on GitHub.**
  `pyproject.toml` sets `readme = "README.md"`, so that file is the PyPI long
  description, and PyPI renders it with no base URL: a relative target is
  resolved against the *project page*. The "example workspace" link arrived as
  `https://pypi.org/project/nextbrief/examples/workspace` and 404'd, and 33
  links in `README.md` and 17 in `README.zh.md` were doing the same thing on the
  page most new users land on.

  All 50 are now absolute GitHub URLs pinned to the release tag —
  `blob/v0.2.0/…` for a file, `tree/v0.2.0/…` for a directory — which is the
  convention the mark above them already used, and for the same reason: PyPI
  keeps every version's long description forever, so a `main`-pinned link sends
  the reader of an old release page to documentation that no longer describes
  it. `scripts/bump-version.sh` sweeps the tag forward with the rest of the
  file. Anchors such as `](#privacy)` need no base URL and are untouched.

  The half-fix is why this is now three guards rather than a commit. The mark
  was moved to an absolute URL for exactly this reason one release ago and the
  links beside it were left relative, because nothing in the suite could see the
  difference — on GitHub, where the page is reviewed, all fifty of them worked.
  `tests/test_docs_consistency.py` now refuses a relative path link, refuses a
  branch-pinned one, and resolves every tag-pinned link against this checkout so
  a renamed file cannot become a 404 at the next release.

## [0.2.0] - 2026-08-09

### Changed

- **The first release on PyPI proper, so `pip install nextbrief` resolves.**
  Every release before this one carried a pre-release segment, and the release
  workflow routes those to TestPyPI — correctly, because publishing a candidate
  to the real index cannot be undone. The cost was that every documented install
  command needed an explicit index URL and an explicit version, and the Claude
  Code plugin shipped in `0.2.0rc4` told a new user to run `pipx install
  nextbrief`, which returned "no matching distribution". This release is what
  makes that line true.

  Both READMEs are rewritten rather than edited: `uvx nextbrief v0`,
  `pipx install --python /usr/bin/python3 nextbrief`, no index URL and no pin.
  The `--python /usr/bin/python3` stays — the nightly job is started by a GUI
  scheduler with a minimal `PATH`, and pinning the system interpreter is what
  keeps a Homebrew Python upgrade from retiring the one a pipx venv was built
  against.

  TestPyPI does not go away, and the docs now state the rule instead of the
  situation: a version with a pre-release segment routes there, a final version
  routes to PyPI, and that is the workflow's behaviour rather than a convention
  anyone has to remember. `/releases/latest/` stops being a 404 and starts being
  something subtler — it resolves to the newest *non-prerelease* while every
  other version string on the page is swept to the last tag — so downloads stay
  on the tagged URL and the reason is written down next to them.

- **`docs/INVENTORY_SCHEMA.md` says who owns each field's vocabulary, instead of
  how stable the field is.** The old per-field promise column restated what
  `schema_version` already covers, equally for every field: nothing can be
  renamed, removed, retyped or added without a bump. What a version number
  structurally cannot tell a consumer is who decides the *values* — and
  `status` has already had its vocabulary renamed once, with no bump, correctly,
  because the document did not change shape. Somebody's registry did. `fixed
  here` is safe to switch over exhaustively; `not ours` is safe to display and
  unsafe to branch on.

### Added

- **Both READMEs open on a brief the tool actually printed.** An excerpt of
  `examples/workspace/BRIEF.md` now sits above the install instructions, cut to
  the three things that are hard to get anywhere else: the evidence line under a
  next action, the open decision naming both the evidence that would settle it
  and where that evidence already sits, and the footer admitting what the
  evidence gate threw away.

  It is not a stored sample. `tests/test_readme_demo.py` rebuilds the example,
  re-renders it, and requires every run of lines between two `…` markers to
  appear verbatim and in order in the file that comes out — and separately
  requires the excerpt to still show those three things, because an excerpt
  trimmed to one heading is still made entirely of real output. A worked example
  is the easiest place in a project for a claim to stop being true: it is
  written once from a real run, the tool moves, and the sample goes on reading
  perfectly, because nothing about a stale sample looks stale.

### Fixed

- **The mark rendered as a broken image on PyPI.** `pyproject.toml` sets
  `readme = "README.md"`, which makes that file the long description, and a
  relative `src` has nothing to resolve against there — so the first thing above
  the fold on the page most new users land on was a broken image. It is now
  served from `raw.githubusercontent.com`, pinned to the release tag rather than
  to `main`, because PyPI keeps every version's long description forever and
  renders it from whatever the URL resolves to at read time.

  The guard that made this unfixable is narrowed rather than removed. It
  forbade any `http` on the mark's line, which cannot be satisfied on PyPI at
  all; what is worth refusing is a host that is not this repository, and that is
  what it checks now.

- **The documented `brew install` had been failing its checksum since
  `0.2.0rc1`.** `scripts/bump-version.sh` sweeps the formula's `url` and
  `version`; it cannot sweep the `sha256`, because that digest belongs to an
  asset which does not exist until the tag is pushed and the release job has
  built it. The two are meant to be rejoined by a second, manual commit, and
  that commit was skipped four releases running — so the formula asked for a
  `0.2.0rc*` tarball and checked it against the `0.1.0rc14` digest. Confirmed
  rather than deduced: the published `v0.2.0rc4` sdist hashes to `210a6cc0…`
  and the formula says `5550a1a0…`.

  The check on that line asserted it was sixty-four hex characters, which a
  four-release-old digest is. Nothing else in the repository knew which release
  the digest came from, so nothing could tell one from the other. The formula
  now records that next to it, and while it disagrees with `version` neither
  README may print the pinned `brew install` — `--HEAD`, which builds from
  `main` and checks no digest, is what is documented until the digest catches
  up.

## [0.2.0rc4] - 2026-08-08

### Added

- **`PRIVACY.md`, and it does not say "nothing leaves your machine".** That
  sentence writes itself for a local tool and is false twice here: stage 2 sends
  `state/digest.json` to whichever model you configured, and `nextbrief probe`
  fetches URLs your own registry declares. The page says which is which, that
  `probe` is opt-in per run and never part of the nightly job, that `nextbrief
  v0` sends nothing at all, and what the digest actually carries — paths, commit
  subjects and prose you wrote, not the contents of your source files. It also
  says what `privacy.never_read` does *not* cover: it is a path filter rather
  than a content filter, it is not retroactive, and it has nothing to do with
  `probe`. [SECURITY.md](SECURITY.md) remains the detailed version and wins
  wherever the two could be read differently.

  No retention or deletion policy is claimed, because there is no server to
  retain anything — and a test refuses the phrases that would imply one, a
  negated mention included. The first draft said there was nothing to "request
  deletion" from, which is true and still the wrong sentence: a reader takes
  "we do not retain your data beyond 30 days" as a promise about a system that
  exists.

- **The icon appears in the READMEs.** `packaging/icon/` has held the artwork
  since the icon was built and neither README had ever shown any of it. The
  colour mark, not the monochrome one: the latter is drawn in `currentColor`,
  which through an `<img>` resolves to black and disappears on GitHub's dark
  theme. Both files were rendered and composited over white and over `#0d1117`
  to check, and a test refuses a mark that depends on `currentColor` so the
  obvious-looking fix for dark mode cannot be applied by mistake.

- **nextbrief installs as a Claude Code plugin, and the skill is read-only by
  lint.** `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`
  make this repository its own marketplace, and the one skill it ships,
  `skills/portfolio-context/SKILL.md`, hands a session the portfolio context
  before it starts work.

  The rhythm is the argument. The brief fires once a day; the context an agent
  reads before it begins fires once per session, which for anyone running many
  projects at once is the stronger habit. It is also why this waited on
  `inventory.json` getting a `schema_version` first: a plugin gives that file
  consumers outside this repository, and versioning a contract after its first
  consumer arrives is versioning it after the first breakage.

  The skill names six commands and no others — `context --json`, `projects`,
  `brief`, `ls`, `show`, `closed`. `nextbrief do` opens a working session and
  `done` / `drop` / `defer` write terminal state, so none of them may appear in
  a skill body. `tests/test_plugin.py` enforces that against the CLI's own
  dispatch table rather than a typed-out list, so a command added to the engine
  tomorrow is covered tonight. Two directions, because they fail differently: an
  allowlist over anything inside a code fence or backtick span, and a denylist
  over the whole file, which catches the state-changing command written in bare
  prose that an agent will act on just the same. Global flags are stepped over,
  so `nextbrief --workspace DIR do NA-0001` is caught as `do` rather than read
  as a command named after the path.

  A skill body is not documentation — it is text another person's agent reads
  and then executes, on a machine this repository will never see. That puts it
  in the category the design contract is already careful about, and makes a
  five-line lint worth more than a paragraph asking a model to be careful.

- **`inventory.json` is a versioned contract.** The document produced by `sense`
  and printed by `nextbrief context --json` now carries a top-level
  `schema_version`, starting at `1`, and
  [`docs/INVENTORY_SCHEMA.md`](docs/INVENTORY_SCHEMA.md) publishes the field set
  behind it: every field marked stable or may-change, and the `kind` domains
  (`declared` / `observed` / `absent`) written down instead of living only in the
  implementation.

  It was the only artifact shipping without one. `snapshot.json` has carried
  `schema_version` since its second shape and `brief.json` has a JSON Schema,
  while the file an agent reads *before it starts work* had nothing — which was
  survivable only while every reader lived in this repository. Once a plugin or
  somebody else's tool reads it, a rename stops costing a test edit and starts
  being a silent breakage in a program nobody here maintains. Versioning it after
  the first consumer arrives is versioning it after the first breakage.

  The envelope is now assembled in `inventory.py` rather than half there and half
  at the call site in `sense`, so one module owns the shape.

  `tests/test_inventory.py` holds the published field set of each version as a
  literal table and compares it against a document from a real `sense` run: add,
  drop or rename a field and it goes red until the version is bumped and the new
  shape recorded. Writing the table out rather than deriving it from the code is
  the point — a derived table agrees with whatever the code currently does.

- **`nextbrief probe`: evidence for work that never lands on disk.** A new
  registry field, `evidence_probe`, names one URL, one count and one date; the
  new command fetches it and caches the reading to `state/probes.json`. The
  result becomes a fourth evidence kind, `probe`, alongside `commit`,
  `file_mtime` and `session`.

  The three original senses all answer the same question -- what happened in this
  filesystem? -- and that question is increasingly the wrong one. Posts written
  into a database behind an admin editor, a migration finished on a hosted CMS, a
  deck on a design tool: the project is moving and the repository is silent, so
  the brief reports a cold project and is wrong in the most confident way
  available to it. A count and a date is exactly the shape a commit already has,
  which is why this is one more sensor on existing machinery and not a new
  subsystem. Keeping it that shape is the design.

  **`sense` never fetches.** Stage 1 reads `state/probes.json` like any other
  file; only the explicit command opens a socket, and
  `test_sense_never_touches_the_network` runs a whole sensing pass with the
  socket layer disabled -- not `urlopen`, the socket layer, so that a fetch added
  later by any path fails the suite. An unattended sensor phoning out nightly
  would convert somebody else's downtime into your failed brief, a site redesign
  into local noise, and a daily outbound request into something you have to
  explain. Reading only your own disk is worth more than the number.

  The cost of that choice is admitted rather than hidden: **a probe reading is
  born old.** Every reading carries `sampled_at`, and past its `ttl_days` the
  brief keeps printing the number -- it is still the best fact available -- with
  its age beside it and a re-sample suggestion carrying the command. An undated
  number is prose, and prose going stale unnoticed is the problem this engine
  exists to solve; introducing a new thing that expires without dating it would
  have been solving it in one direction while breaking it in another.

  **A failed probe is never a zero.** Timeouts, non-200s, and a route that
  quietly starts serving HTML are each recorded with their own error code and
  announced in the brief as a banner above the fold, keeping the last good
  reading beside them with its age. A broken sensor reads 0, and 0 is
  indistinguishable from "nothing happened".

  The boundary is checked rather than promised: https only, GET only, no
  `Authorization`, no cookies, no userinfo in the URL, only URLs the registry
  declared, no redirect off that origin, a 2 MiB cap and a timeout. The selector
  language has no evaluation in it. `probe` joins `commit` and `session` as a
  kind the evidence gate checks strictly -- it is the only kind whose facts are
  not on your machine, so it is the only one the reader cannot go and verify by
  looking, and a claim dressing a file mtime up as a probe reading borrows that
  authority without earning it.

- **A criterion the design moved past can now say so.** `-` in the `done` tick
  selector drops the criterion under the cursor: not done, not outstanding, no
  longer applicable. The instruction line is now move / toggle / drop / finish,
  and terminals that cannot draw the list get `-2` alongside the tick numbers.

  Acceptance criteria had two boxes and a design change needs three. Ticking an
  obsolete criterion claims work that never happened -- the false completion this
  project refuses everywhere else. Leaving it unticked reads as a shortfall, and
  that one does not sit still: unticked criteria are what `done` drafts as
  `future_work`, and `followup` turns those into real backlog items carrying
  `discovered_from`. So abandoning a goal minted a task for it, and the next
  reader had nothing to tell them the work was dead. A wrong record is static; a
  wrong record that mints tasks travels.

  Dropped means marked, not deleted, in the sense `drop <id>` already uses -- the
  line stays in the file and so does its sentence, because erasing the words
  would erase the only thing worth keeping, which is that the goal moved. The
  mark is `- [~]`, and all four readers of criteria share one parser so that
  none of them can quietly forget it: a mark three readers know about prints
  `AC 2/5` as `AC 2/4`, which does not look like a bug, it looks like an item
  that always had four criteria.

  No new question. `-` acts on one keypress, and a criterion set aside during the
  run pre-fills the `summary` **draft** -- `dropped 2 criteria: ...` -- taken by
  `=`, editable, and skipped by Enter exactly like every other draft. The closing
  questions stay at two, both skippable.

- **`(agent)` and `(you)`: criteria now say who can settle them, and `done` only
  asks you about yours.** The marker goes in the criterion's own text, after its
  number -- `- [ ] #4 (you) the sample export reads right to you`.

  This was measured, not designed. Three items that could not be closed carried
  20 acceptance criteria between them, and exactly 2 needed the author: one UAT
  and one set of credentials. The other 18 were things a command could settle,
  and every one of them sat in the same list, in the same shape, in front of the
  same person. So the expense was never the ticking -- it was that "which of
  these actually need me" had to be worked out from scratch on every single
  close, which is precisely the context switch this tool exists to spend rather
  than charge.

  **The question is who can tell it is true, not who does the work.** Those come
  apart constantly: only a person can choose the illustrations, but "three files
  appeared in `assets/`" is something one command can see, so that criterion is
  the agent's. `(agent)` is the default; `(you)` is for direction, UAT, access,
  resources an agent cannot obtain, and judgement given as the user.

  Held back is not hidden. The ones `done` does not ask about are counted out
  loud, and what stays open still drafts as follow-up work exactly as before -- a
  list quietly shorter than the file is this project's characteristic bug, the
  one that reads as an item that was always this small. `--all-criteria` puts
  them back on the list, which is what setting one of the agent's aside looks
  like: a criterion the design moved past is most often the agent's, and without
  a way to reach them the `- [~]` mark would be unreachable for the case it was
  built for.

  **An unmarked criterion counts as yours**, and that is load-bearing rather than
  lenient. Every criterion written before this existed carries no marker, so
  reading the absence as "the agent's" would empty the tick selector for an
  entire backlog in one move -- and being askable at all is the whole point of
  the step. `check` reports how many are still unclassified instead of the engine
  guessing on their behalf.

- **`check` warns about criteria nobody can answer.** Two rules, one line each
  however large the backlog: items with criteria carrying no `(agent)`/`(you)`
  marker, and items putting more than two criteria on you -- which is a problem
  with the item, not with the person. One line *per item* is what this obviously
  wanted to be and is exactly what would have killed it, at twenty-odd warnings
  on the first run of any real workspace. The exit code is untouched: 3 still
  means out of date, and an awkwardly worded item is not a reason for a scheduler
  to re-run the pipeline.

- **`closed` shows where the goals went, not only what shipped.** Criteria the
  design moved past are listed under their own `~`, kept out of the follow-up
  lines above them, and counted in a footer saying nobody is meant to pick them
  up. They had no shape here at all before: a set-aside criterion appeared only
  if somebody happened to mention it in the summary, so a project's history read
  as though it had always meant exactly what it shipped.

- **`show` says how much of an item is yours before you read it.** A header above
  the file prints the open criteria marked `(you)` in full and counts everything
  else, then the file follows byte for byte. The file cannot show this on its
  own: the criteria are one flat list in one shape, and the two that need a
  person look exactly like the seven that do not.

- **The locale check now covers the CLI's keys too.** It read `.t(key)`, and the
  CLI does not use it -- the CLI asks through `tr(cat, key, fallback)`, and that
  fallback is exactly what makes a missing key silent there. It is not a visible
  breakage; it is perfectly good English printed in the middle of a Chinese
  session, with nothing anywhere saying why. `tests/test_i18n.py` now reads the
  keys out of the CLI's own syntax tree and requires every one of them in both
  catalogs.

  This closes the gap rather than a live bug: the two keys that had shipped
  missing this way -- the tick prompts, the first thing a person sees when
  closing an item -- were added to both catalogs in `556b90f`, and every key the
  CLI asks for is present today. The check exists so the next one cannot repeat
  it, which is the half that was missing when those two got through. Keys are
  read from the syntax tree rather than by pattern because two of them are built
  by concatenation, and a regex reports the prefix as absent -- a guard that
  fails over a key nobody ever asked for is a guard somebody deletes.

### Fixed

- **The skill no longer assumes the engine is installed.** A plugin ships
  skills; it does not install a Python package. So the whole path for somebody
  arriving from a directory was `/plugin install` → success → `nextbrief:
  command not found`, and the one person who would never see it is the author,
  whose `PATH` has had the engine on it the whole time. Same shape as the
  dogfooding gap this project already knew about, moved out one layer: the
  plugin had only ever been tried on the machine where its precondition was
  already true.

  `skills/portfolio-context/SKILL.md` now opens with `nextbrief --version`, and
  says what to do when the shell answers `command not found` — report it, with
  `pipx install nextbrief` or `uv tool install nextbrief`, and do not run the
  install on somebody's behalf. Installing software is the owner's decision and
  this skill only reads. Both manifests say the same thing where a directory
  visitor sees it, before they install anything.

  The guard runs the skill's own first command on a `PATH` the engine is not on
  and requires the exit code the shell really returns to be one the skill has
  named. Written first against the phrase "command not found", which the skill
  happens to use twice — the mutation that deleted the sentence that mattered
  left the incidental mention behind and the test stayed green. The number
  appears once, and is compared against the live exit code rather than a
  constant.

  Shipping the engine inside the plugin was considered and rejected: `bin/` at
  a plugin root does put executables on the Bash tool's `PATH`, and the zipapp
  this repository already builds runs as a bare command on a system Python at
  283 KB. Three things stopped it. Two builds of the same tree do not produce
  the same bytes, so a committed artifact cannot be guarded by rebuild-and-
  compare and would drift from `src/` silently. `/plugin install` could not be
  exercised here, and choosing an install-time mechanism on the strength of a
  documentation paragraph is the exact mistake this entry is about. And `bin/`
  would put `done`, `drop`, `defer` and `do` one token away in every Bash call
  of every session, which is a different plugin from the read-only one the
  lint has been defending.

- **The plugin manifests now cite a `$schema` that exists.**
  `.claude-plugin/marketplace.json` shipped
  `https://anthropic.com/claude-code/marketplace.schema.json` from the day it
  was written, and that URL has never resolved -- so the file carried the one
  field whose presence says it has been validated, while never having been
  validated against anything. `plugin.json` claimed no schema at all, which was
  the more honest of the two states and also useless.

  Both now point at the SchemaStore schemas generated from Claude Code's own
  definitions: `claude-code-marketplace.json` and
  `claude-code-plugin-manifest.json`. Checked rather than guessed -- each
  returns 200 with a draft-07 schema whose `$id` is the URL it was fetched
  from, the SchemaStore catalog maps them to `**/.claude-plugin/marketplace.json`
  and `**/.claude-plugin/plugin.json`, and Anthropic's own repository replaced
  the dead link with the same URL. A plausible-looking `$schema` that also 404s
  would be worse than none, because it looks like the check was done.

  `tests/test_plugin.py` pins the two URLs *per file*, because they differ by
  one word and the copy-paste that swaps them resolves, validates, and validates
  against the wrong shape. It also repeats the required-field sets those schemas
  declare, since `$schema` is ignored at load time and nothing at runtime would
  notice a manifest that failed it.

  Neither manifest gains a `version`, deliberately. With none set, the plugin's
  version resolves to the source repository's commit SHA, so an installed plugin
  updates whenever this repository moves; an explicit version would mean users
  receive a skill fix only when the field is bumped, and the skill changes
  between releases. `claude plugin validate` passes with a warning about it;
  only `--strict` treats that warning as an error.

- **A release bump no longer deletes the previous release from the README.**
  `scripts/bump-version.sh` sweeps the new version through the READMEs and the
  Homebrew formula, because it also lives in badges, install commands and
  download URLs. The sweep was an unbounded `replace`, and README.md had since
  grown an append-only release-history table -- so every bump rewrote the newest
  row's version into the release being cut while leaving that row's CHANGELOG
  anchor and publication date pointing at the release it used to describe. The
  row still parsed, the table still rendered, nothing went red, and a release
  vanished from the public index. It happened on `0.2.0rc1` and again on
  `0.2.0rc2`, corrected by hand both times.

  The sweep is now bounded by a marker pair in the document itself
  (`<!-- bump-version:skip:begin -->` … `:end`), rather than by a heading name --
  the heading differs between the two languages and either can be renamed by
  someone who never opens the script. An unclosed marker stops the release
  instead of guessing which of its two readings was meant, and the run now
  prints how many references it left inside the boundary, so a marker doing its
  job and a marker somebody deleted stop looking the same in the log.

- **`nextbrief --workspace DIR init` no longer scaffolds into the current
  directory.** `--workspace` and `--out` are declared on the parent parser every
  subcommand inherits, but `init` is dispatched before any workspace is resolved
  -- it is the command that creates one -- and reads only its positional
  argument. Both flags parsed, bound to nothing, and the run carried on as
  though they had been honoured.

  `nextbrief --workspace /tmp/safe init -y --no-scan`, typed from this
  repository's root, scaffolded `config.jsonc`, `registry.jsonc`, `prompts/`,
  `schema/`, `backlog/`, `log/`, `state/` and `.claude/settings.json` into the
  public tree, the settings file carrying the owner's absolute home paths. The
  output did name the directory it had really written to -- on the line nobody
  reads, having just said where to go.

  Both are now a usage error on `init`, named individually and answered with the
  form that works (`nextbrief init DIR`), before anything is written. Refused
  rather than reinterpreted: `--workspace` means "the existing workspace to
  operate on" for every other command, and redefining it as "where to create
  one" for this one alone would trade a visible failure for an invisible one.
  `--locale` is read above the dispatch and genuinely acted on, so it still is.
  This is the defect class the `ShippedConfigTemplate` docstring already
  records -- a flag accepted and read by nothing is worse than a flag that does
  not exist, because argparse's silence reads as consent.

## [0.2.0rc3] - 2026-08-07

### Fixed

- **An interrupted `done` no longer closes the item.** Ctrl-C at the closing
  questions was caught in the same branch as EOF and treated as "skip", so the
  command carried on: it wrote `status: done`, set `human_confirmed: true`, and
  committed. An interrupt was being recorded as the reader confirming something
  they were trying to back out of.

  The two are not the same event. EOF is a pipe running dry or a non-tty run --
  nobody is there to answer, which genuinely means skip. Ctrl-C is somebody
  stopping the command. Enter already skips, and the prompt says so, so Ctrl-C
  never needed to.

  This reverses a deliberate decision, and half its reasoning was right: an
  interrupt that merely exits leaves the reader unsure whether the close
  happened. The answer to that is to *say* nothing happened -- `done` now prints
  "Cancelled. <id> was not closed and nothing was written." -- rather than to
  close the item anyway.

  The same conflation is fixed in `review`, where Ctrl-C partway through used to
  save the answers given so far. `nextbrief do` already had it right and is
  unchanged.

- **The engine no longer counts its own output as the project's uncommitted
  work.** `walk_project` hid `state/`, `log/`, `BRIEF.md` and `BRIEF.html` from
  the activity count, for the reason `engine_output_globs` gives -- a workspace
  declared as one of your own projects would otherwise never go stale, because
  the thing measuring it touches it every night. That reasoning never reached
  git, so the same files came back as UNCOMMITTED: a parked workspace reported
  stalled, the evidence term read +3 for a repository nobody had touched, and
  `check` could not settle, because each run dirtied the project it had just
  measured.

### Added

- **`done`, `drop` and `defer` say which item they are about to close.** All three
  write `human_confirmed: true` and commit, so a mistyped id permanently confirms
  an item nobody has read and leaves a commit saying so — and the id is typed by
  hand, where `NA-0017` and `NA-0019` differ by one keystroke. `do`, which only
  opens a session, already printed a header; the three that cannot be undone
  printed nothing. They now print `> {id} · {title}`, the project, and how many
  acceptance criteria are ticked, before anything is asked or written.

  The count is there because it is the number that stops you. An item closed at
  `0/6` is either finished-and-unticked or not finished, and both are worth one
  second of hesitation; nothing displayed it anywhere before.

- **A draft for each of the two closing questions, derived only from what is
  already on disk** — the project's commits since the item was opened, and its
  acceptance ratio. No model, no network: `done` has to stay instant or it stops
  being typed.

  **The draft is shown above the question and is never what Enter means.** Enter
  still skips, exactly as before; `=` takes the draft; typing gives your own
  words. If Enter took the draft, the reflex that answers every form would start
  producing machine sentences signed by a person — a fabricated finding, which is
  worse than the empty field it replaces, because an empty field at least says
  nobody knows.

- **`summary_source: human | accepted_draft | none` in the closing record**, so
  the record can say whose sentence it holds. A record written before this field
  existed carries no `summary_source` at all, which is a fourth and different
  claim from `none`.

### Changed

- **`nextbrief followup` stopped printing a `.` nobody could read.** The list
  marked unpromoted entries with `  .`, a placeholder aligning to a `-> NA-0026`
  that a freshly closed item has never shown — a legend legible only to someone
  who no longer needs it, using a character that already means "unconfirmed" in
  `ls`. The column now appears only once something has been promoted, says
  `already NA-0026` / `not promoted` in words, and is padded by display width so
  a CJK translation does not shift every column to its right.

- **`nextbrief followup --promote` says what it will create before creating it.**
  It mints files and makes two commits per item, and it used to describe that
  afterwards.

## [0.2.0rc2] - 2026-08-06

### Added

- **`nextbrief defer <id> --until <date|reason>` — the verb that was missing.**
  `done` and `drop` were the only two ways an item could leave the page, and the
  commonest thing that actually happens to work is neither: it is still true,
  still worth doing, and not now. Recording that as `drop` writes a falsehood
  somebody has to rebuild later; leaving it open keeps it competing for a place
  it cannot win.

  `--until` is required, and that is the safety property: **a deferral that never
  returns is a drop nobody recorded.** A date is taken as the date. Anything else
  is taken as the condition you are waiting on — a good reason and a useless
  trigger — so the item also gets a review date (`defer.review_after_days`,
  default 30) and comes back to be looked at again.

  Nothing is written to bring it back. `items.is_live` reads the date, so a
  workspace nobody ran for a fortnight still shows everything that came due
  meanwhile, and the brief names the items that returned that morning. `deferred`
  joins the terminal statuses in gate 3: an agent able to park an item could hide
  work nobody would ask about again.

- **A closing record on `nextbrief done`: `summary` and `future_work`.** The
  moment an item stops being open is the moment it carries the most information
  and the last moment anyone can say so, and a boolean was consuming all of it.
  An item reading "run 3 probes" whose truth was "migrated all of them" leaves
  behind a *false* history if only the status is recorded.

  Two questions, both skippable — the count is the design. A form that costs more
  than it returns is answered with Enter inside a fortnight, and empty fields
  look like findings. Flags (`--summary`, `--future-work`) skip the prompt
  entirely, and a non-tty run never asks.

  No new store: the record is written into the item's own file under a
  `SECTION:CLOSING` block, which is already in `backlog/` and already in git.

- **`nextbrief followup <id>` promotes future work into real items**, each
  carrying `discovered_from` back to where it came from, with the new id written
  beside the original entry — so a follow-up nobody picked up stays visibly
  unpicked. Without this, `future_work` would be another field written and never
  read.

- **`nextbrief closed [project]`** reads the records back, grouped by project,
  and counts the closed items that left none — which is the honest measure of
  whether the habit is sticking. **`nextbrief ls --deferred`** shows what is
  parked and until when.

### Changed

- **`proposed_status` is read.** The prompt has always told agents to suggest a
  closure rather than perform one, and nothing in the engine had ever read the
  field — so the safe action was also the silent one. Standing proposals are now
  listed in both artifacts under **waiting for your confirmation**, with the
  commands that answer them, and `done` / `drop` / `ok` clear the field so the
  question is asked once rather than every morning.

### Fixed

- **`check` reported every workspace out of date seconds after a run.** Token
  counts and the last-active timestamp live in `projects[].sessions`, which
  `canonical()` compares, and both move on every assistant turn. For a tool whose
  audience uses agent sessions, that is always — so `check || run` degraded to
  `run`, and `check` lost the ability to say anything at all.

  Sharper than that: running `nextbrief check` from inside an agent session writes
  to that session's transcript, moving the number the check is about to compare.
  It could not settle even in principle.

  Both fields are excluded from the comparison and not from the snapshot. What
  belongs in `check` is what reaches the page, and neither of these does — tokens
  are deliberately never printed as magnitudes, and the page reads
  `last_active_date` rather than the timestamp. The day count, the date and the
  file count all still count, and a test names that set, so widening the
  exclusion later has to break something rather than quietly deafen the check.

- **Building the zipapp could overwrite the builder's own brief.** The smoke test
  unset `NEXTBRIEF_WORKSPACE` but not `NEXTBRIEF_OUT`, so on a machine that
  exports both, `--workspace` moved the inputs to the throwaway tree while the
  outputs stayed pointed at the real one: a brief for an empty workspace written
  over the real `BRIEF.md`, and a fictional run appended to the real logs. All
  four `NEXTBRIEF_*` variables are scrubbed now, in the script and in the test
  that invokes it.

## [0.2.0rc1] - 2026-08-06

### Added

- **An app icon, and the tooling that rebuilds it** — `packaging/icon/`. An ivory
  sheet carrying two rules and a deep teal check: a brief, and the gate every
  claim passes before it prints. Drawn at 16px first, because a Notification
  Center banner and a menu bar are where it actually lives; everything larger is
  the same drawing at more pixels.

  Ten iconset members, 16 through 1024, and a valid `.icns` confirmed by an
  `iconutil` round trip. `verify-iconset.py` checks each member against the
  geometry rather than trusting the render — sheet coverage, ink weight, the
  check's position and its share of the tile — so a silently broken export fails
  the build instead of shipping.

  It is not in the wheel, and that is not an oversight. cc-notify derives a
  banner's icon from process ancestry, not from a path a caller hands it, so a
  copy inside `site-packages` would be read by nothing. It ships for packagers —
  a Homebrew cask, an `.app` bundle — and becomes useful to notifications the day
  cc-notify grows a flag for it.

- **Notifications can go through [cc-notify](https://github.com/hancheng-ai/cc-notify),
  under nextbrief's own identity.** macOS draws a banner's icon and its
  Notification Center grouping from the *sending* app, so shelling out to a bare
  `terminal-notifier` means sending as `fr.julienxx.oss.terminal-notifier` — the
  identity every other tool doing the same thing also uses — and a nightly brief
  lands in one undifferentiated pile with everything else on the machine.

  cc-notify had already solved that for itself, and grew a `--send` mode so other
  local tools could too. `auto` now prefers it where it is installed and falls
  back to the platform sink where it is not, which is most machines — so the
  fallback is the ordinary path rather than the exceptional one.

  Its exit code is the contract: `0` only when a banner was actually delivered.
  That is what makes trying it first safe, because an unauthorized bundle id
  fails *silently* on macOS, and a sink that reported success there would mute
  the run without saying so.

  Measured: `ai.hancheng.cc-notify.nextbrief` for the brief against
  `ai.hancheng.cc-notify` for session events — distinct senders, separate groups.

- **The brief says what is new, not only what is true.** One line under the
  header: *"Nothing has newly stalled or gone quiet since <the last run>"*, or the
  projects that have newly gone quiet or newly lost their next step, named.

  The counts in the header say what is true. They do not say what changed, so a
  morning with two stalled projects read identically whether both stalled last
  week or one stalled overnight — and a document that reads the same every day
  teaches its reader to skim it.

  One line rather than a shorter document on quiet days. A brief whose *shape*
  varies is one whose reader no longer knows where to look, and the saving would
  be a few lines they were not going to read anyway. Nothing is hidden: the full
  brief is still underneath, and the line is permission to stop reading it.

- **`render --check`**, and `nextbrief check` now runs both deterministic stages.
  It used to be `sense --check` alone, which compares the snapshot and the
  digest — so a workspace whose snapshot was current reported current however old
  `BRIEF.md` was, and even when there was no `BRIEF.md` at all. A scheduler
  running `check || run` therefore never re-ran, which is the single outcome the
  exit code exists to prevent.

  It writes nothing, including the run record: a check that mutates what it is
  checking is not a check.

### Fixed

- **A `session:` citation could be minted for a project with no sessions.** The
  scan creates a project's entry as soon as a directory name matches, so one that
  has never had an agent session — or whose transcripts have since been cleaned
  up, which is the ordinary end state — carried a block full of zeros. A dict of
  zeros is truthy, and the handle was minted on that.

  The gate resolves a cited source and checks that it can supply that kind of
  fact. Neither check looks at magnitude. So the handle resolved, the kind
  matched, and a model could write "three agent sessions this week" about a
  project with none, printed under a footer promising every claim was checked.

- **`git: "none"` is now checked rather than believed forever.** The registry
  beating the overlay is a rule about *judgements* — importance, phase,
  positioning — because nothing else can measure those. Whether a directory is a
  repository is not a judgement, and the declaration goes stale the moment
  somebody runs `git init` in it.

  Until now the brief printed "a bad delete is unrecoverable" every morning about
  a repository that had been recording every change all along. A false warning is
  worse than a frequent one: acting on it wastes the reader's time, and not
  acting on it teaches them to skip the column.

  Declared and observed sit side by side and neither is written over the other.

- **A neglected or stalled project no longer interrupts you every morning.**
  Those two were state tests — "is anything neglected" — while the two branches
  above them diff against the previous snapshot. Measured on a real portfolio,
  that reason drove 29 of 40 recorded runs.

  Edge-triggered now, against what the reader has actually been *told*. That set
  advances only when a notification is delivered, and shrinks as projects
  recover, so: a run told `--no-notify` or whose sink was broken does not consume
  the edge; a project that is announced, recovers and relapses is announced
  again; and a notification that never lands keeps being retried, which reaches
  nobody while the transport is down and says what has been true all along on the
  first run after it is fixed.

  No timer, which is the better answer than one: a timer re-fires about a project
  you already know about.

- **A crowded brief loses its least valuable section, not its last one.**
  Truncation kept a prefix, so the footer went first — the line naming what
  generated the document and stating that every claim passed the evidence gate —
  and the cut landed wherever the line count fell, which for a portfolio of any
  size is inside the projects table.

  Whole sections now, chosen by an explicit table rather than by position.
  Reading order and drop order are different questions: the reminders are written
  near the bottom because that is where they read best, and they are the last
  thing given up because they are the brief's only warnings.

  The default ceiling goes from 60 lines to 100. Measured on a twelve-project
  workspace the brief wants 72, so the gate fired every single morning, and a cap
  that always fires is not bounding a document — it is deleting the end of it.
  Not derived from the project count: a ceiling that rises to meet its contents
  is not a ceiling.

- **`review --web` no longer waits on a name server before opening the browser.**
  `HTTPServer.server_bind` sets `server_name` from `socket.getfqdn(host)`, and
  nothing in `webform` ever reads `server_name` — the URL is built from the
  literal loopback address and the port the OS handed back. On a machine whose
  resolver has nothing to say about `127.0.0.1`, binding therefore blocked until
  that lookup timed out, entirely to compute a string no line of code asks for.

  It was seconds on the macOS runners, where it failed three socket tests on
  every build while passing everywhere else, and it was the same silent wait
  between someone typing `review --web` and their browser appearing. A command
  whose first act is an unexplained pause reads as broken.

  The regression test asserts that binding *does not call* `getfqdn`, rather than
  that binding is fast. A duration assertion on a shared runner is a flaky test,
  and the defect was never about the duration — it was about asking a question
  whose answer is thrown away.

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

- **The repository publishes what the public needs, and nothing that is only
  about the maintainer.** The pre-push fence is now `scripts/leak-shapes.py`: the
  generic shapes that are never publishable whoever they belong to — an absolute
  home path, a private key header, a connection string carrying a password, a
  token.

  The checks it replaces read an out-of-band identifier list and a private
  directory, so published they served exactly one person while their
  documentation described that person's arrangement — a check whose own
  docstring is a disclosure. They now live outside this repository, and the hook
  a maintainer runs calls this file first, so a defect in the published fence is
  found here rather than by a contributor.

  What a contributor gets is therefore narrower and honest about it. `--worktree`
  also searches untracked files now: it is the check you run *before* committing,
  and `git grep` without `--untracked` reads only tracked ones, so a leak in the
  file you were about to add was invisible to it.

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

  It covers the one case the hook cannot: hooks are not cloned, so a contributor
  who never ran that line has no check at all. What it scans for names nobody, so
  its output is safe in a public log.

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

- **`scripts/leak-shapes.py`, and a `pre-push` hook that runs it.** A push that
  would publish an absolute home path, a private key header, a connection string
  carrying a password or a token is refused before the objects leave the machine.

  The fence is local. A CI job runs after the push, a pull request is public from
  the moment it opens, and a force-push does not retract objects that have
  already landed — so a remote check is a report rather than a fence. Activate it
  once per clone with `git config core.hooksPath .githooks`, repo-local rather
  than global, so it governs this repository and nothing else on the machine.

  It scans commits, not the working tree, because a push publishes history and a
  clean tip says nothing about what is behind it. What it matches is a *shape* —
  recognisable without knowing whose it is — which is both why it can print its
  findings in a public log and why it is a floor rather than a guarantee. The
  case it structurally cannot catch is a real example that has been relabelled,
  and the defence against that one is the rule in `CONTRIBUTING.md`.

- **`nextbrief describe <id> --capability "<text>"`** — what the thing built here
  could also serve, as against what it currently does. Always declared, never
  derived: a manifest says what a package is, and no file says that something
  generalises beyond its current use.

### Changed

- The identifier scan is gone from CI. It could only ever run its weakest check
  there — the rest needs a local configuration a runner does not have — and what
  it did run, it ran too late to matter.

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

[Unreleased]: https://github.com/hancheng-ai/nextbrief/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/hancheng-ai/nextbrief/releases/tag/v0.2.0
[0.2.0rc4]: https://github.com/hancheng-ai/nextbrief/releases/tag/v0.2.0rc4
[0.2.0rc3]: https://github.com/hancheng-ai/nextbrief/releases/tag/v0.2.0rc3
[0.2.0rc2]: https://github.com/hancheng-ai/nextbrief/releases/tag/v0.2.0rc2
[0.2.0rc1]: https://github.com/hancheng-ai/nextbrief/releases/tag/v0.2.0rc1
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
