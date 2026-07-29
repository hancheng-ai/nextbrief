# Contributing to nextbrief

The engine is small on purpose, and most of it is deliberate. This file tells
you which parts are settled, which parts are actively looking for help, and what
a patch to each one should look like.

If you only read one section, read [The four extension points](#the-four-extension-points).

---

## Getting set up

```bash
git clone https://github.com/hancheng-ai/nextbrief
cd nextbrief
python3 -m unittest discover -s tests -v
```

**No install step, and that is not an oversight.** `tests/helpers.py` puts `src/`
on `sys.path` itself, so the suite runs against the checkout on a bare
interpreter -- which is the only setup guaranteed to exist on the 3.9 floor
described below.

You only need an install to get the `nextbrief` command itself, and on 3.9 that
takes one extra line:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip     # <- required on 3.9
.venv/bin/python -m pip install -e .
.venv/bin/nextbrief --version
```

The upgrade is not hygiene. The build backend is `hatchling`, so an editable
install goes through PEP 660, which pip only learned in 21.3 -- and the pip
bundled with macOS's system 3.9 is 21.2.4. Skip the line and pip reports
`File "setup.py" or "setup.cfg" not found`, which sounds like a broken project
rather than a stale pip. On 3.11 or newer the bundled pip is new enough and the
line is a no-op.

That is the whole toolchain. There is no `pytest`, no `tox`, no `make`. The test
suite is `unittest` from the standard library, and it runs on any Python the
package supports.

Before opening a PR:

```bash
python3 -m unittest discover -s tests -v
python3 -m pip install ruff && python3 -m ruff check .
```

CI runs both across macOS and Linux on Python 3.9, 3.11 and 3.13, plus four
guard jobs: zero runtime dependencies, locale catalogs in sync, a clean
`twine check` on the built artifacts, and a scan for personal identifiers.

### Python 3.9 is the floor, and it is a hard floor

Not conservatism -- a deployment fact. The nightly run is started by a GUI
scheduler, which hands the process a minimal `PATH` and whatever interpreter the
operating system ships. On macOS that is still 3.9. If the engine needs a
virtualenv to run, it does not run.

So: no `match`, no `X | Y` unions evaluated at runtime, no `tomllib`, no 3.10+
standard library APIs. Put `from __future__ import annotations` at the top of
every module and annotate freely -- annotations are strings and cost nothing.

### Zero runtime dependencies

`[project.dependencies]` is empty and CI fails if it ever is not. Same reasoning:
an unattended job that has to import a third-party package is an unattended job
that breaks the first time an environment shifts under it. It also means
`pip install nextbrief` can never conflict with anything you already have.

This applies to runtime only. Development extras (`build`, `twine`, `ruff`) are
fine, because nothing on the nightly path imports them.

If you find yourself wanting a dependency, the answer is usually that the
feature belongs in a **provider** or a **sink** -- a module that is imported only
when configured, and that may shell out to a tool the user already has
installed. That is what those directories are for.

### No personal data, ever

nextbrief was extracted from one person's private workspace. Real project names,
hostnames, absolute home paths and prose from that workspace must never appear
in this repository -- not in code, not in docs, not in a fixture, not in a test
that reproduces a bug. CI greps every tracked file and fails the build on a hit.

Write fictional examples. `Fernwood` and `atlas-api` do the job as well as
anything real, and a fixture you invented is one you can edit freely when the
parser changes.

Two things about that CI check are worth knowing before you rely on it.

It cannot list every name it should catch, because a denylist kept in the repo
publishes exactly what it is protecting. Only names already public are spelled
out in `ci.yml`; the rest come from a `PRIVATE_IDENTIFIERS` repository secret,
one per line, and that pass reports the *file* it matched without echoing the
matched text — a public repo has public CI logs. On a fork PR the secret is
absent, that pass is skipped, and the job says so.

And it only ever catches the names it was told about, which is a much weaker
guarantee than a green check suggests. Relabelling a real example defeats it
entirely: swap the project name and every specific that made the example worth
reaching for — a file and line number, a status, a date — survives the rename and
is still somebody's real detail. Concrete examples are much better than vague
ones. Invent the concreteness rather than borrowing it.

---

## The design contract

This is the part that is settled. Every one of these was paid for; if a patch
strains one, say so in the PR rather than working around it quietly.

**The pipeline is three stages, and only the middle one is a model.**

| Stage | Kind | In | Out |
|---|---|---|---|
| sense | deterministic | filesystem + git | `state/snapshot.json` |
| interpret | a model | snapshot digest | `state/brief.json` |
| render | deterministic | brief + snapshot | `BRIEF.md`, `BRIEF.html`, logs |

1. **sense and render are deterministic.** The same inputs produce byte-identical
   outputs, timestamps excepted. Never sort, group, or bucket by wall clock --
   the self-check that exits `3` when the snapshot would change is what makes the
   nightly job safe to run twice, and it is easy to break by accident.

2. **The model only interprets.** Every decision that can be made in Python is
   made in Python. Prompts do not carry business rules, because a prompt is an
   instruction and instructions get drifted from.

3. **Every rendered claim carries evidence, and the renderer checks it.** Claims
   in `brief.json` reference sources; the renderer resolves each one against the
   snapshot and *drops what it cannot resolve* into `log/rejected.jsonl`. This
   check lives in the renderer rather than the prompt on purpose --
   [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) explains why at length. Do not
   move a check from the renderer into the prompt to make output prettier.

4. **No agent sets a terminal status.** Nothing automated may write `done` or
   `dropped`. It may write `proposed_status`, which a human confirms. A false
   completion is far more expensive than a missed one, because the missed one
   comes back tomorrow and the false one never does.

5. **The engine writes nothing outside the workspace.** It reads your projects;
   it writes only its own directory. This is what makes it safe to point at
   everything you own. `Workspace.contains()` exists for exactly this check --
   use it.

6. **Fail open.** A parser that cannot understand its input returns `None` and
   records the path in `parse_failed`. It does not raise. One malformed file
   must never cost you the whole brief, and a silent gap that is *recorded* is
   recoverable in a way that a crashed nightly job is not.

---

## The four extension points

Everything above is closed. These four are open, and contributions to them are
the ones we can review quickly and merge with confidence -- each is a small
surface with a clear contract and no reach into the core.

### 1. `providers/` -- a new model backend

The interpret stage is one function call. A provider takes a prompt and returns
text; it knows nothing about briefs, evidence, or backlogs.

```python
run_provider(name, cfg, prompt, ws) -> ProviderResult
```

**A good provider PR:**

- shells out to a CLI the user already has, or speaks HTTP with
  `urllib.request`. No SDK, no new dependency.
- reads its settings out of `cfg` -- model name, effort level, timeout -- and
  hard-codes nothing.
- returns a `ProviderResult` on failure too, with the error captured. The
  pipeline degrades to a model-free brief; it does not crash. A provider that
  raises has broken rule 6.
- records cost and token counts when the backend reports them. This project has
  strong opinions about cost (see the architecture doc) and cannot hold them
  without numbers.
- is tested with a fake subprocess or a stubbed opener, never a live call.
- documents the auth it expects in its module docstring: environment variable,
  existing CLI login, nothing else. Do not add config keys that hold secrets.

**Wanted:** local models via an OpenAI-compatible endpoint, and any hosted API
that can be reached with `urllib`.

### 2. `sinks/` -- a new notifier

Where the brief announces itself when it is ready.

```python
notify(title, body, cfg) -> bool
```

**A good sink PR:**

- returns `False` on failure instead of raising, and never blocks. A notifier is
  the least important thing in the pipeline; it must not be able to fail the run.
- respects quiet hours and the "only when something changed" rule that the caller
  applies -- do not re-decide that inside the sink.
- keeps its body plain text and short. Every sink gets the same two strings; if
  yours needs structured data, that is a sign the change belongs upstream.
- degrades when its transport is missing: no `notify-send` on this box means
  return `False`, not a traceback.
- times out. An unreachable webhook must not hang a nightly job forever.

**Wanted:** Linux desktop notifications, a generic webhook, terminal-bell/stdout
for headless boxes.

### 3. `locales/` -- a new language

The brief is user-facing prose. Catalogs live in `src/nextbrief/locales/`, one
file per locale, and every string the user reads comes through `cat.t()`.

**A good locale PR:**

- adds exactly one file, with **the same key set as `en`**. CI diffs the key sets
  and fails on any difference, so a partial translation is caught before review.
- keeps `{placeholders}` intact and lets the plural forms work -- `t()` takes
  `count=`; use it rather than concatenating a number onto a noun.
- translates meaning, not words. Several strings are terse on purpose because
  they sit in a table cell; if a literal rendering will not fit, write the short
  version that means the same thing.
- leaves the key names alone. Keys are English identifiers regardless of locale.

If you find a user-facing string hard-coded in Python instead of the catalog,
that is a bug worth its own small PR -- moving it into the catalog is a welcome
first contribution.

### 4. Parsers -- reading another project's status doc

The most valuable of the four. The sense stage looks at each project you own and
tries to answer: when did this last move, what does it say its status is, and
what has it decided *not* to do. It does that by reading whatever document that
project already keeps. Every format it learns is one more project that appears in
your brief with no extra bookkeeping.

A parser is a pure function from text to facts, which makes it the easiest thing
in the codebase to test and to review.

**A good parser PR:**

- adds a fixture under `tests/fixtures/` and tests against it. Include a messy
  case, not just the clean one -- real documents have trailing whitespace, a
  half-finished table, a heading someone renamed.
- **returns `None` rather than guessing.** This is the whole discipline. A date
  scraped out of a sentence becomes a deadline in someone's brief, and they will
  believe it. Only structured, unambiguous positions count: a frontmatter field,
  a table cell, a `Status:` line. Prose is not evidence.
- reads a bounded prefix of the file. Status documents can be enormous and the
  useful part is near the top.
- normalizes both sides before comparing. If you match a heading or a
  non-goal string, the document's spacing and punctuation will differ from your
  pattern's, and a comparison that ignores that will silently never match.
- records the path in `parse_failed` when it bails, so the gap is visible in the
  brief instead of looking like "nothing happened here".
- extracts non-goals **verbatim**. They are quoted back to the model as things
  not to propose; a paraphrase defeats that.

**Wanted:** Keep a Changelog, GitHub issue exports, `TODO.md` conventions,
sprint files from common templates, and the status conventions of any tool you
already use.

---

## Sending the change

- Branch from `main`, one topic per PR. A parser and a locale in the same diff
  take three times as long to review as the two apart.
- Comments explain **why**. The code already says what. If you made a tradeoff or
  rejected an obvious alternative, that sentence is the most valuable line in
  the patch -- this codebase is written that way throughout and we would like to
  keep it that way.
- English in code, comments, docstrings, and commit messages.
- Add to `CHANGELOG.md` under `## [Unreleased]` if the change is user-visible.
- Do not bump the version in `pyproject.toml`. Releases are cut by tag.

---

## Releasing (maintainers)

Everything after the tag is automated, and both indexes use Trusted Publishing --
there are no API tokens anywhere in this repository.

1. Move the `Unreleased` section of `CHANGELOG.md` into a dated version heading.
2. Set the version in `pyproject.toml`. That value is the source of truth; the
   release workflow refuses to publish a tag that disagrees with it.
3. Tag a release candidate and push it:

   ```bash
   git tag v0.2.0rc1 && git push origin v0.2.0rc1
   ```

   Any pre-release version -- `rcN`, `aN`, `bN`, `.devN` -- routes to **TestPyPI**.
   Install from there and run it against a real workspace before going further:

   ```bash
   pip install -i https://test.pypi.org/simple/ nextbrief==0.2.0rc1
   ```

4. When it holds up, tag the final version. A version with no pre-release segment
   routes to **PyPI**, and a GitHub Release with generated notes is created from
   the same artifacts.

Use the normalized PEP 440 form in the tag (`v0.2.0rc1`, not `v0.2.0-rc1`) --
the workflow compares it against `pyproject.toml` as a literal string and will
stop rather than publish a mismatch.
