# `inventory.json` — the published field contract

`state/inventory.json` answers **what exists**, as against `state/digest.json`,
which answers what moved. It is written by `nextbrief sense` on every run, and
`nextbrief context --json` prints the same bytes — so everything below applies
identically to both.

This file exists because that artifact is about to be read by things that are not
in this repository. Until it was, a field could be renamed in a commit and the
only cost was a test edit. Once something outside reads it, the same rename is a
silent breakage in somebody else's tool, on a day they were doing something
else — and silent breakage is the failure mode this whole project was built to
refuse.

So: here is what you may depend on, and here is how you will be told when it
changes.

---

## Read the version first

```json
{
  "schema_version": 1,
  "generated_at": "2026-02-01T21:30:00",
  "root": "~/projects",
  "projects": [ /* … */ ]
}
```

`schema_version` is the version of the **field set**, not of the engine. The
engine's version is in `nextbrief --version` and moves far more often.

**If you read a `schema_version` you have not seen, stop rather than guess.** A
best-effort parse of an unknown shape is how a consumer ends up confidently
reporting the wrong thing, which costs more than reporting nothing. Refuse, say
which version you understand, and let a person look.

A bump means at least one of: a field renamed, removed, retyped, or a sentinel
value given a new meaning. **Adding a field counts too** — the number exists so a
reader can tell a shape they have seen from one they have not, and a shape with
an extra key is one they have not seen. What does *not* bump it is the obvious
thing: the values change on every run, because that is the job.

Every bump gets an entry in [`CHANGELOG.md`](../CHANGELOG.md) naming the old and
new shape.

`schema_version` starts at `1` and does not track `snapshot.json`'s `2`. They are
separate contracts with separate consumers; making the numbers match today would
only promise that they keep matching.

---

## The value-vocabulary column

**This column is not about shape.** `schema_version` already covers shape, and it
covers every field equally: nothing here can be renamed, removed, retyped or
added without a bump, so *within a version* the structure is fixed throughout.
A per-field stability tier would only restate that.

What a version number cannot tell you is **who decides the values**. That is what
this column is for.

- **fixed here** — the set of values this field can take is defined in this
  repository. Safe to compare, join on, or switch over exhaustively.
- **not ours** — the value arrives from the user's own config, or from a
  heuristic reading their files. Safe to display; **unsafe to branch on**. The
  shape will not move without a version bump, and the vocabulary will move
  without one, because it was never ours to freeze.

The distinction is load-bearing rather than tidy. `status` is a plain string in
both versions of this document, and its vocabulary **has already been renamed
once** — `tier` became `status` plus `positioning`. No `schema_version` bump
announced that to a consumer switching on the old words, because the *document*
did not change shape; somebody's registry did. That failure is invisible to
versioning by construction, and this column is the only warning of it.

Every key documented here is **always present** regardless of column. `null`
means "we looked and there was nothing there"; a *missing* key means you are
reading a different `schema_version` than you think.

---

## The envelope

| Field | Values | Type, and what it is |
|---|---|---|
| `schema_version` | fixed here | integer. The version of this contract. |
| `generated_at` | fixed here | string. ISO-8601 local time, second precision, no zone suffix. Copied from the snapshot's run, so a brief and an inventory produced by the same run carry the same instant. |
| `root` | not ours | string. Absolute path to the directory the projects were sensed under, on the machine that produced the file. Meaningless anywhere else, and the field that would have to change if an inventory ever covered more than one root. |
| `projects` | fixed here | array of objects, **sorted by `id`**, one per sensed project. May be empty. Never `null`. |

## A project entry

| Field | Values | Type, and what it is |
|---|---|---|
| `id` | fixed here | string. The join key — the same id the backlog, the digest and the brief use. |
| `name` | fixed here | string. Display name; falls back to `id`. |
| `path` | fixed here | string or `null`. The project's directory **relative to `root`**. `null` when the project declares no path. |
| `description` | fixed here | object. What this project is, plus where that sentence came from. See below. |
| `capability` | fixed here | object. What was built here that generalises past its current purpose. See below. |
| `goal` | not ours | string or `null`. One line, free text, from the registry's `goal_one_line`. The field and the registry key already disagree on their name, which is exactly the kind of thing that gets tidied. |
| `stacks` | fixed here | array of strings, sorted, from the manifests actually present: `node`, `python`, `claude-plugin`, `rust`, `php`, `go`. May be empty. |
| `run` | not ours | array of strings, at most 8. How a person runs this, lifted from `package.json` scripts (first 6, sorted) and `pyproject.toml` entry points. Heuristic and capped, so its contents are a convenience rather than a promise. |
| `declared` | fixed here | boolean. `true` if the owner listed this project in the registry, `false` if discovery found it. The difference matters: one is a decision, the other is a directory. |
| `status` | not ours | string or `null`. The project's lifecycle tier — conventionally `active`, `maintenance`, `frozen`, `done`. **Do not switch exhaustively on it**: the vocabulary is scored from the user's own `config.jsonc` and has been renamed once already. |
| `positioning` | not ours | string or `null`. Free text the owner typed about where this sits in the portfolio. |
| `serves` | fixed here | array of strings. Project ids this one serves. Empty array, never `null`. |
| `needs` | fixed here | array of strings. Project ids this one depends on. Empty array, never `null`. |
| `unlocks` | fixed here | array of strings. Project ids unblocked by this one. Empty array, never `null`. |
| `has_git` | fixed here | boolean. Whether the project directory is a git repository. |

## `description` and `capability`

Both are the same three fields, and the third one is the point.

| Field | Values | Type, and what it is |
|---|---|---|
| `what` | fixed here | string or `null`. The sentence itself. `null` exactly when `kind` is `absent`. |
| `kind` | fixed here | string, from a closed set. Where the sentence came from — see the domains below. |
| `source` | fixed here | string or `null`. What to go and check: a filename like `package.json` or `README.md`, or the literal `registry`. `null` exactly when `kind` is `absent`. |

**Why the split is load-bearing.** "orchard is a tenancy API" is in its
`package.json` and you can go and read it. "orchard is our flagship" is a thing a
person typed. Both are useful; blending them makes the second read as a finding.
`kind` is how a consumer tells them apart, and it is the reason this artifact is
worth reading at all rather than just grepping the tree.

### `description.kind`

| Value | Meaning |
|---|---|
| `declared` | The owner wrote this sentence in the registry. `source` is `registry`. An assertion, not an observation. |
| `observed` | Lifted verbatim out of a file in the project. `source` names that file, and you can check it. |
| `absent` | We looked in every manifest and every README and found nothing. `what` and `source` are both `null`. This is a real finding, not an error — it is the one thing a person can fix in ten seconds. |

### `capability.kind`

| Value | Meaning |
|---|---|
| `declared` | The owner wrote it. `source` is `registry`. |
| `absent` | Nobody has. `what` and `source` are both `null`. **This is the normal case** — most projects will never have one. |

There is deliberately **no `observed`** here, and there never will be without a
version bump. No file on disk says "the scheduling core in here would serve a
domain it was never written for". That is a judgement about potential, and a
consumer must be able to see it is reading somebody's optimism.

---

## Sentinels, precisely

Three things that look alike and are not:

- **`null`** — we looked, there is nothing. A fact.
- **`""`** — someone wrote an empty string. Shouldn't happen; if you see one,
  that is a bug worth reporting.
- **key missing entirely** — you are not reading the `schema_version` you think
  you are. Check it and stop.

Array fields are `[]` when empty. They are never `null`, and you never have to
guard for it.

`kind: "absent"` always travels with `what: null` and `source: null`. Those three
move together, and a consumer may rely on checking only `kind`.

---

## How this stays true

`tests/test_inventory.py` carries the field set of every published version as a
literal table and compares it against a document produced by a real `sense` run.
Add a field, rename one, or drop one, and that test goes red until
`INVENTORY_SCHEMA_VERSION` is bumped, the new set recorded, and this file
updated. The table is written out rather than derived from the code on purpose:
a table derived from the code would agree with whatever the code currently does,
which is the one thing it must never do.
