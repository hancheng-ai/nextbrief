# Architecture

nextbrief answers three questions every day: what moved, what you should do
next, and what is stuck. It answers them across every project you own, from the
files those projects already keep.

The interesting part is not that a model is involved. It is where the model is
*not* involved, and what stands between it and the page.

---

## Three stages

```
                    ┌──────────────────────────────────────────┐
                    │  your projects (read-only, always)       │
                    │  status docs · git history · file mtimes │
                    └────────────────────┬─────────────────────┘
                                         │
   stage 1   sense        deterministic  │  no model
                                         ▼
                    ┌──────────────────────────────────────────┐
                    │  state/snapshot.json     complete facts   │
                    │  state/digest.json       compact subset   │
                    └──────────┬───────────────────┬───────────┘
                               │                   │
                       digest  │                   │  snapshot
                               ▼                   │  (kept back)
   stage 2   interpret   ┌───────────┐             │
             a model     │  provider │             │
             (the only   └─────┬─────┘             │
              stage that       │                   │
              may be wrong)    ▼                   │
                    ┌──────────────────────┐       │
                    │  state/brief.json    │       │
                    │  claims + evidence   │       │
                    └──────────┬───────────┘       │
                               │                   │
   stage 3   render            ▼                   ▼
             deterministic  ┌─────────────────────────────┐
             no model       │  gate 1  evidence           │──┐
                            │  gate 2  non-goals          │  │ dropped /
                            │  gate 3  write permission   │  │ reverted /
                            │  gate 4  caps               │  │ deferred
                            └──────────────┬──────────────┘  │
                                           │                 ▼
                    ┌──────────────────────┴──────┐   log/rejected.jsonl
                    │  one gated dataset          │   log/deferred.jsonl
                    └───────┬─────────────┬───────┘
                            ▼             ▼
                       BRIEF.md      BRIEF.html
```

Stage 1 and stage 3 are ordinary Python with no model in them. They are
deterministic: the same inputs produce byte-identical outputs apart from
timestamps, which is why the whole pipeline can be re-run at any time and why a
self-check can exit `3` to mean "this would change something".

Stage 2 is the only place a model appears, and it is given a deliberately narrow
job: read pre-computed facts, induce structure over them, cite each statement.
It never sees the full snapshot, cannot write a file, and cannot close anything.

---

## Why the evidence check lives in the renderer

This is the single design decision the rest of the system is arranged around.

The obvious way to stop a model inventing progress is to tell it not to. Put it
in the prompt, in bold, near the top: *do not claim anything you cannot support.*

That works, most of the time. Which is the problem.

An instruction is a request to a process that is allowed to interpret it. It
competes with everything else in the context window, and it loses gradually and
invisibly: a long day with sparse signal, a project with nothing to report, a
model that would rather produce a useful-looking sentence than an empty section.
Nothing announces the failure. You get a brief that reads exactly like the
briefs that were correct, and you act on it. The failure mode of an
instruction-based guarantee is a confident false statement you cannot
distinguish from a true one.

So the guarantee is not asked for. It is *imposed*, one layer downstream.

Every claim in `brief.json` carries an `evidence` array, and every entry in it
names a source. The renderer resolves each source against the evidence index in
`snapshot.json` — the file the model never saw. A claim whose evidence resolves
is rendered. A claim whose evidence does not resolve **is not rendered at all**;
the original text goes to `log/rejected.jsonl` with a reason code.

The difference is categorical:

|  | instruction in the prompt | check in the renderer |
|---|---|---|
| enforced by | the model's cooperation | code, every run |
| failure mode | plausible false statement | statement missing, logged |
| detectable | no | yes — count in the brief, line in the log |
| degrades over time | yes | no |
| survives a model swap | no | yes |

An instruction can be drifted from. A renderer that drops unverifiable claims
cannot drift — it has no capacity to be persuaded. The model may hallucinate as
freely as it likes; hallucinations do not have resolvable evidence, so they do
not reach the page. "Do not invent progress" stops being a hope about a model's
behaviour and becomes a property of the pipeline.

The same reasoning applies to length. A prompt that says *be concise* decays.
A renderer with a hard cap does not, so the caps are gate 4 rather than a
sentence in the prompt.

The cost is real and worth naming: a *true* claim that the model failed to cite
properly gets dropped too. That is the trade, and it is accepted deliberately.
A brief that is quietly missing something stays trustworthy — you notice the
gap, and the count of dropped claims is printed in the brief itself. A brief
containing one confident fabrication is not trustworthy anywhere.

This also means the model must have a way to say *nothing happened*. It does:
an explicit "no signal since `<date>`" form, which is the only legitimate use of
an evidence entry with no underlying source. Without that escape hatch, the
pressure to produce content turns into pressure to fabricate it.

---

## The four gates

All four live in stage 3. All four are deterministic. All four leave a record.

### Gate 1 — evidence

Each claim's `evidence[].source` must resolve in the snapshot's evidence index.
Rejections are recorded by kind, which makes drift measurable rather than
anecdotal:

| reason | meaning |
|---|---|
| `no_evidence` | the claim arrived with an empty evidence array |
| `unresolvable_evidence` | the source does not exist in the snapshot |
| `evidence_kind_mismatch` | the cited source exists but is not the kind of fact claimed |
| `bad_none` | a "no signal" claim used where a real source was required |

Dates get an extra rule: deadlines are only ever read from the registry, where a
human wrote them. A date found in prose is never promoted to a deadline, no
matter how confidently it is cited.

### Gate 2 — non-goals

Projects declare what they have decided *not* to do, and the sense stage lifts
those declarations verbatim into the snapshot. Proposals that collide with one
are flagged in the brief, **not removed**.

This is the one gate that marks rather than blocks, and the asymmetry is
intentional. Matching is textual, so it will sometimes be wrong. Silently
deleting a good suggestion is a worse error than showing a suggestion with a
warning beside it — the first is invisible, the second is one glance to dismiss.

Normalization matters here: both the declared non-goal and the proposal text are
stripped of separators and punctuation before comparison, because the same
phrase written by two people never agrees on spacing.

### Gate 3 — write permission

Backlog items are files with structured frontmatter, and stage 2 is allowed to
touch some fields but not others. The renderer diffs each item field-by-field
against its committed version in git and reverts anything out of bounds, logging
the attempt.

| an agent may write | an agent may not write |
|---|---|
| `updated_date` | `priority`, `is_next_action` |
| `status: open ↔ waiting` | `status: done`, `status: dropped` |
| `proposed_status` (a suggestion) | `human_confirmed` |
| the notes section | acceptance-criteria checkboxes |

The prohibition on terminal statuses is the load-bearing one. A missed item
resurfaces tomorrow; a falsely closed item never resurfaces at all, and you stop
looking for it. Nothing automated may close anything — it may only propose, and
a human confirms.

Because the baseline is `git HEAD`, human edits must be committed as they are
made. Otherwise the gate cannot tell "the owner marked this done" from "an agent
wrote `done`", and it would revert the owner's own work.

### Gate 4 — caps

Section-by-section limits on how much reaches the page. Overflow is written to
`log/deferred.jsonl` and counted in the brief, so a cap never loses information —
it only defers it.

A cap is also the only honest way to keep a daily document readable. The brief
is *physically* unable to grow past its limits, regardless of how much the model
produced.

### One dataset, two renderings

`BRIEF.md` and `BRIEF.html` are rendered from the same gated data structure. The
HTML re-decides nothing: no re-ranking, no re-filtering, no second opinion. They
therefore cannot disagree, and a change to a gate lands in both at once.

---

## The floor: what the engine can touch at all

The four gates above decide what reaches the page. This one decides what reaches
the disk, and it is not numbered among them because it is not part of the render
pass — it holds for every stage, every command, and every code path that has ever
opened a file for writing.

All of it lives in `nextbrief.fs`, which is the only module in the package that
mutates a filesystem. Everything else calls into it.

**Containment.** Nothing outside `Workspace.root` or `Workspace.out` may be
created, modified or removed. This is what makes it safe to point the engine at a
directory holding a dozen unrelated repositories: the neighbours are input, and
the engine is structurally unable to treat them as output. Not a rule a
contributor has to remember — a precondition on every mutating call, with no
unchecked function left to reach for.

**Human-only paths.** Backlog entries, `registry.jsonc` and `config.jsonc` cannot
be deleted or renamed. Gate 3 already refuses to let anything automated write a
terminal status, and deletion reaches that same end state by a shorter route: an
item that is gone is closed, and closed without the record that it was ever open.
The registry and config are protected for a second reason — they declare what the
engine may look at, so a run able to delete them is a run able to widen its own
scope. Directories are refused outright. There is no recursive delete in this
package and nothing has needed one.

**Declared exits.** Three things legitimately live outside every workspace: the
pointer file recording your default workspace, the agent settings file that
`permissions --merge-into` edits, and the backup taken beside it. Each names
itself against a list in `fs.ESCAPES`, and an undeclared reason raises. The list
is short and hand-maintained on purpose — it is the review surface, and adding to
it should be conspicuous in a diff rather than a one-line call at some new site.
Neither `sense` nor `render` imports the escape at all, which is asserted by a
test: no unattended nightly run can write outside a workspace by any path.

One deliberate asymmetry. Log appends are fail-open about the *environment* — a
full disk costs a log line, never the run — but not about the *target*. A path
outside the workspace raises, because that is a bug in the caller, and a bug that
returns `False` is a bug that ships.

---

## Cost, as measured

The figures below are measurements from the reference workspace the engine was
developed against — around a dozen projects and a working backlog — not
estimates, and not a benchmark you should expect to reproduce exactly. The
*shape* is what transfers.

The sense stage writes two files. `snapshot.json` is complete and is what the
renderer resolves evidence against. `digest.json` is a compact projection of it
and is the only thing the model receives. That split is not tidiness; it is the
entire cost story.

| what the model was given | rounds | output | cacheRead | per run |
|---|---|---|---|---|
| read each backlog file individually, plus a ~104 KB snapshot twice | 36 | 66.8k | 3.24M | **$4.37** |
| one read of a ~25 KB `digest.json` | 9 | 38.8k | 410k | $1.09 |
| the same, at low reasoning effort | 7 | 14.5k | 238k | **$0.74** |

Two things fall out of this, and both are counter-intuitive enough to be worth
stating plainly:

**Cached input cost is roughly rounds × context size, so round count dominates —
not file size.** The expensive version was not expensive because the snapshot
was large. It was expensive because fourteen separate file reads meant
thirty-six agent turns, and every one of those turns re-read the entire
accumulated context. Collapsing the same information into one pre-assembled file
cut the bill by a factor of four while giving the model *the same facts*. The
optimization is not "send less data", it is "send it in fewer turns".

**High reasoning effort buys very little for structured induction over
pre-computed facts.** Most of the output tokens in the second row were thinking.
But stage 1 has already done the work that reasoning would otherwise have to
reconstruct — the dates are computed, the signals are classified, the non-goals
are extracted verbatim. What remains is grouping and phrasing over a table of
known values, and dropping the effort level cut output tokens by nearly two
thirds with no loss of quality. Reasoning effort pays where the model must
*derive* facts. It is close to wasted where the facts arrive pre-derived.

This is the general lesson of the architecture pointed at the bill rather than
at correctness: every unit of work moved out of the model and into deterministic
Python makes the run cheaper *and* makes it more trustworthy. The two objectives
do not trade off here. They point the same way.

There is headroom left. Model CLIs typically support a stripped invocation that
skips plugin discovery, tool registration and project-file auto-loading, which
would remove a large block of cache-write cost — usually at the price of
requiring an API key instead of an existing interactive login. That trade has
not been taken.

---

## Three properties that are easy to lose

**Determinism.** The self-check re-runs the sense stage and compares the result
to what is on disk; it exits `3` when they differ. That check is only meaningful
if nothing sorts, groups, or buckets by wall clock. It is very easy to break by
accident — a "sort by recency" that uses `now` instead of the run's `as_of` date
looks identical for six hours a day and then does not.

**Fail-open.** A parser that cannot understand a file returns `None` and records
the path in `parse_failed`; it never raises. External tools are optional and
their absence is recorded, not fatal. One malformed document in one project must
not cost you the brief for all the others — and a gap that is written down stays
recoverable, while a crashed nightly job just means you find out tomorrow.

**Privacy paths are structural, not advisory.** The registry can mark paths that
must never be read. For those, the sense stage records a single integer count —
the contents are not read and *the filenames do not enter the snapshot either*,
because a filename is often the sensitive part. Since nothing about them reaches
the snapshot, nothing about them can reach the model or the page. The rule is
written in both the configuration and the code comment, in the hope that it
survives future edits to either.
