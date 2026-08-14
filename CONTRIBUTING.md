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

CI runs both across Linux, macOS and Windows on Python 3.9, 3.11 and 3.13, plus
four guard jobs: zero runtime dependencies, locale catalogs in sync, version
literals in agreement, and a clean `twine check` on the built artifacts. On
Windows the POSIX-only developer scripts skip rather than fail — see
[Windows](#windows) for what that boundary is and why.

### Windows

**nextbrief runs on Windows. It is not developed on Windows.** Those are two
different promises and only the first one is made.

Running is supported and tested: the engine is stdlib-only Python, and
`windows-latest` is in the test matrix on the same three interpreters as the
other platforms. If the CLI misbehaves on Windows, that is a bug — report it.

Developing is not. Four things in this repository are POSIX and are staying
that way:

| What | Why it is not portable |
|---|---|
| `scripts/build-zipapp.sh` | builds the release artifact; runs on the release machine, which is Linux CI |
| `scripts/bump-version.sh` | cuts a release; same machine, same reason |
| the release-notes step in `release.yml` | a bash heredoc driving `gh` |
| `.githooks/pre-push` | a `sh` script whose activation is a POSIX mode bit |

The tests covering those skip on Windows rather than fail, through
`requires_posix_dev_env` in `tests/helpers.py`. That guard asks whether this is
a POSIX developer environment, **not** whether `bash` is installed — Git for
Windows puts `bash.exe` on `PATH`, so the older `shutil.which("bash")` guard was
true on `windows-latest`, the sh-only tests ran there, and eight of them failed.
A suite that is red for something nobody undertook to support is
indistinguishable from a suite that is red for something broken, which is the
same class of mistake as a gate that never ran.

So: to work on the engine on a Windows machine, use WSL. To *use* the engine,
you need nothing but Python.

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

Real project names, hostnames, absolute home paths and prose copied out of a
private directory must never appear in this repository -- not in code, not in
docs, not in a fixture, not in a test that reproduces a bug. A push that would
add any is refused before it leaves your machine.

Write fictional examples. `Fernwood` and `atlas-api` do the job as well as
anything real, and a fixture you invented is one you can edit freely when the
parser changes.

Three things about that check are worth knowing before you rely on it.

**It runs locally, and there is no remote equivalent.** `scripts/leak-shapes.py`
is the implementation and `.githooks/pre-push` runs it on the commits a push
would add. Turn it on once per clone:

```
git config core.hooksPath .githooks
```

Repo-local rather than global, so it governs this repository and nothing else on
your machine.

**CI runs it too, and that is a report rather than a fence.** CI runs after the
push, a pull request is public from the moment it opens, and a force-push does
not retract objects that have already landed — unreferenced commits stay
retrievable by SHA. A check that can only report a leak after publishing it is
not a weaker fence, it is a report. The `leak-shapes` job earns its place for the
single case the hook cannot cover: hooks are not cloned, so a contributor who
never ran the line above has no check at all. Minutes rather than never.

Do not treat that job going green as this section being satisfied. Turn the hook
on.

**It catches shapes, and a shape is a low bar.** An absolute home path, a private
key header, a connection string with the password still in it, a token — things
recognisable without knowing whose they are. That is deliberate: it is why the
check can live in a public repository and print its findings in a public log
without disclosing anything itself.

What it structurally cannot catch is the case that actually happens. Relabelling
a real example defeats it entirely: swap the project name and every specific that
made the example worth reaching for — a file and line number, a status, a date —
survives the rename and is still somebody's real detail. There is no shape left
to match on.

So the guarantee is much narrower than a green check suggests, and the real
defence is the rule at the top of this section rather than the script. Concrete
examples are much better than vague ones. **Invent the concreteness rather than
borrowing it.**

**This has already happened here, and it is worth knowing how it looked.** A
command's `--until` example wanted a project to wait on, and a real one from the
author's private notes was to hand. It read perfectly — that is the whole trouble
with a borrowed example, it is concrete because it is real. It reached seven
tracked files: a README in both languages, a docstring, a usage string, both
locale catalogs, a schema comment, and a test. Two more names had come in the
same way through a test fixture. All of it shipped in two release candidates
before anyone looked, because the fence that would have stopped it had never been
switched on.

Three things that cost more than the fix did:

- **The shape scan was green the entire time, correctly.** A borrowed name has no
  shape. Whatever the `leak-shapes` job says, it has not read your examples.
- **An example spreads.** One line in a README became ten occurrences across two
  languages and a test suite, because a good example gets reused — which is what
  makes borrowing one worse than it first looks, not better.
- **The gate had never once been watched failing.** It was written, reviewed, and
  documented, and `core.hooksPath` was never set, so it had never run at all. An
  uninstalled gate and a gate that passed write the same thing in the log:
  nothing. `scripts/gate-selfcheck.py` exists so that absence has a voice —
  `tests/test_gate_selfcheck.py` runs it against your clone, and CI runs it to
  confirm the fence is still able to fail.

Run this once per clone and the story above stops being available to you:

```
git config core.hooksPath .githooks
```

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

4. **No agent takes an item off the page.** Nothing automated may write `done`,
   `dropped` or `deferred`. It may write `proposed_status`, which a human
   confirms. A false completion is far more expensive than a missed one, because
   the missed one comes back tomorrow and the false one never does — and parking
   an item has the same effect on the reader as closing it, in a word that does
   not look like a closure.

   The other half of that rule is newer and just as load-bearing: **a proposal
   channel nothing reads is worse than no channel at all**, because the safe
   action becomes the silent one. `proposed_status` is collected into the brief's
   "waiting for your confirmation" section and cleared when a human answers. If
   you add another thing an agent may only *suggest*, put the reader-facing half
   in the same commit.

5. **The engine writes nothing outside the workspace.** It reads your projects;
   it writes only its own directory. This is what makes it safe to point at
   everything you own. `Workspace.contains()` exists for exactly this check --
   use it.

6. **Fail open.** A parser that cannot understand its input returns `None` and
   records the path in `parse_failed`. It does not raise. One malformed file
   must never cost you the whole brief, and a silent gap that is *recorded* is
   recoverable in a way that a crashed nightly job is not.

7. **Watch every guard fail before you trust it.** After writing a test, revert
   the line it covers, run that test, and require it to go red. Then put the line
   back.

   This is two minutes and it is not optional, because a test that cannot fail is
   indistinguishable from one that passes. Four shipped in a single stretch of
   work here, and each was green for a *different* reason: a loop whose body
   never ran, because the fixture left nothing to iterate; an assertion on the
   wrong file, checking `runs.jsonl` for a write that goes to `deferred.jsonl`; a
   fixture that short-circuited upstream, so the code under test was never
   reached; and a substring match that found what it wanted in a URL rather than
   in the column it meant. Every one of them looked exactly like a passing test.

   Two corollaries, both learned the same way:

   - **The fixture has to be able to reach the code.** A whole-tree assertion
     that nothing was written still passes if nothing in the fixture could have
     triggered the write. Assert that the trigger happened, too.
   - **Prefer the real entry point for anything with a record-keeping seam.** A
     unit test on a hand-built dict confirmed the same reasoning three rounds
     running while the wiring underneath it was wrong.

   **On macOS the loop itself can lie, so watch it too.** The pinned
   `/usr/bin/python3` (3.9.6) caches bytecode *outside* the repository, under
   `~/Library/Caches/com.apple.python/<absolute path of the source>/`. Those
   `.pyc` files carry a `flags` field of `0` -- timestamp invalidation -- so the
   cache is reused whenever it agrees with the source on **whole-second mtime
   and byte size**. Both halves are coarse, and the edit this rule asks for
   often changes neither: `INVENTORY_SCHEMA_VERSION = 1` to `= 2` preserves the
   byte count, and a revert typed briskly preserves the second.

   That buys two failures, and they point in opposite directions:

   - **Revert inside the mutation's second and the mutant outlives the revert.**
     The mutating run compiled and cached it; the revert restores the same size
     and the same whole second, so the cache still matches and every later run
     keeps executing the mutant. The guard looks red when it is not.
   - **Mutate inside the warm run's second and the mutant never runs at all.**
     The previous run's cache still matches, so nothing is recompiled and the
     test passes on code you did not write. The guard looks unable to fail when
     it is perfectly able to.

   `git diff` is clean through both, which makes this the "reached the right
   answer down the wrong path" case the rule exists to prevent -- so the rule
   needs a guard of its own. Four defences, and they are cheap:

   - run mutations under `python3 -B` **and** `PYTHONDONTWRITEBYTECODE=1`, so
     neither the harness nor anything it spawns can write a cache;
   - `os.utime()` a distinct mtime after every write, so a size-preserving edit
     still invalidates a cache that survived anyway;
   - purge `~/Library/Caches/com.apple.python/<repo path>/` before and after;
   - after each revert require the same test to go **green again**, so a
     poisoned cache surfaces on the mutation that caused it rather than as a
     baffling result fifteen steps later.

   The tell, if you ever meet a result you cannot explain: `grep`, `sed` and an
   `ast.parse()` of the file all report the original while a fresh `import`
   reports the mutant -- with `module.__file__` pointing at the very file you
   just read.

   `scripts/watch-red.py` implements all four and drives the mutation list in
   `tests/mutations.json`. Prefer it to doing this by hand.

   Most entries break one line in one file. A guard whose subject is a
   *relationship* — the formula's `sha256` and the `brew install` the README
   prints against it — has to have both ends broken at once, or it stays green
   for the honest reason that the tree is still consistent. Those entries list
   `edits` rather than one `file`/`old`/`new`. Reach for it when a mutation
   only goes red in some of the states the repository passes through: a guard
   that can be watched today and not after the next release is one the summary
   will keep reporting as broken, and a summary with a standing exception in it
   is one nobody reads to the end.

8. **An item's id is allocated, never eyeballed.** Use `nextbrief new
   "<title>" --project <id>`. It takes the next free id and writes the file in
   the same command; `followup --promote` does the same thing from a closing
   record. Do not read the directory and add one.

   Both halves of that command matter, and the second is the one that looks
   optional.

   It counts over **the working tree**, not `git HEAD`. An entry that has been
   created and not committed is invisible to the write-permission gate, to
   anything reading git history, and to the next person doing arithmetic — and
   that is not a hypothetical: on the night this rule was written, three backlog
   files were in exactly that state.

   And it **writes the file as it picks the number**. Two sessions nine hours
   apart each took "the highest id, plus one" off the same directory, and both
   were right about what they had seen, because neither had written anything
   down yet. Two files ended up claiming `NA-0043`, one of them a P0. An
   allocator that prints an id and trusts you to use it reproduces that exactly;
   the gap between deciding and recording is the whole bug.

   That narrows the window to one command rather than closing it, so
   `nextbrief check` **fails** — exit 1, not a warning and not the exit 3 that
   means "re-run me" — when two files claim one id, and every command that
   resolves an id refuses rather than picking. It has to be a refusal: `done`
   used to close whichever file the directory listing reached first, printing
   the same success line it prints when it is right.

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

## Sign your work

This project uses the [Developer Certificate of Origin](DCO) (DCO 1.1). It is not
a copyright assignment; it is a statement that you have the right to submit what
you are submitting, under the project's existing licence.

Add a sign-off line to every commit:

    git commit -s -m "your message"

which appends:

    Signed-off-by: Your Name <your.email@example.com>

Use your real name and an address you can be reached at. By signing off you
certify the DCO, whose full text is in the DCO file at the repository root.

This project's licence will not change: nextbrief stays Apache-2.0. The DCO
exists so provenance is traceable, not so the licence can be altered later.

---

## Releasing (maintainers)

Everything after the tag is automated, and both indexes use Trusted Publishing --
there are no API tokens anywhere in this repository.

1. Set the version with the script, never by hand:

   ```bash
   scripts/bump-version.sh 0.2.0rc1
   ```

   **The version is not in one place.** Three files carry it as a machine-readable
   literal -- `pyproject.toml` (what pip and PyPI see), `src/nextbrief/__init__.py`
   (what `nextbrief --version` prints, and the only one a zipapp can read: an
   archive has no installed metadata, so `importlib.metadata` is not available
   inside one), and `CITATION.cff` (what a citation resolves to). It also appears
   in prose and URLs across `README.md`, `README.zh.md` and the Homebrew formula,
   and the script sweeps those too -- updating only the three machine-readable
   ones left badges and install commands pointing at the previous release, which
   the docs-consistency tests caught one tag too late.

   The script also moves `CHANGELOG.md`'s `Unreleased` section into a dated
   heading for the new version, leaves a fresh empty `Unreleased` behind it, and
   adds the compare/tag links at the bottom. It does *not* blanket-replace inside
   the changelog, or inside the release-history table in either README: those are
   append-only records of releases that already happened, and a blanket replace
   rewrites the last one out of existence. The boundary is a pair of
   `<!-- bump-version:skip:begin -->` / `:end` markers.

   Editing any of this by hand is how you get a package whose `--version`
   disagrees with its own metadata, which nobody notices until a bug is filed
   against a version that was never released.

   `pyproject.toml` remains the source of truth for publishing; the release
   workflow refuses to publish a tag that disagrees with it.

   PEP 440 normalized form only -- `0.2.0rc1`, never `0.2.0-rc1`. The script
   rejects the shapes that would pass locally and fail in CI, where the mistake
   costs a tag.
2. Read the diff the script produced before going further. It touched seven
   files; the changelog heading and the release-history rows are the two places
   a wrong answer is invisible.
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
