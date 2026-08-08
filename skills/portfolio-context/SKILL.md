---
name: portfolio-context
description: Read the portfolio context before starting work in a repository that nextbrief tracks — what projects exist, what each one is for, what is open, and what was closed recently. Use at the top of a session in such a repository, and whenever the question is "what is this project", "what should I pick up here", "what already does this", "what depends on this", or "has this been decided before". Read-only: every command it runs only reads.
---

# Portfolio context

`nextbrief` keeps one workspace that answers two different questions about a
whole portfolio: **what exists** (an inventory) and **what moved** (a daily
brief). Reading it costs one command and replaces walking a dozen directories.

Everything in this skill only reads. Nothing here changes a file, opens a
session, or takes an item off the page.

## First: check the engine is actually here

**This plugin ships a skill, not the engine.** Installing the plugin does not
install `nextbrief`; the two travel separately, and on a machine that has never
had the engine every command below is a `command not found` rather than an
answer. Start here:

```bash
nextbrief --version
```

If that prints a version, carry on to the next section.

If the shell answers `command not found` — exit `127` — **stop and say so.** Do
not fall back to reading directories by hand: the whole value of this skill is
that the answers are checked against evidence, and a hand-rolled substitute has
none of that. Report that the engine is missing and give the person these two
lines, which install it from PyPI:

```
pipx install nextbrief
uv tool install nextbrief
```

It is a Python package with no dependencies and needs no virtualenv of its own.
For a single run without installing anything, `uvx nextbrief` works too. The
other install routes are in
https://github.com/hancheng-ai/nextbrief#install.

**Tell the person; do not run the install yourself.** Putting software on
somebody's machine is their decision, and this skill only reads.

## Check the version before you trust the fields

`state/inventory.json` is a published contract, and the first thing to read in
it is `schema_version`.

```bash
nextbrief context --json
```

That prints the inventory verbatim, for a consumer rather than for a person.

**This skill knows `schema_version: 1`. If you read any other number, stop and
say so.** Do not parse a shape you have not seen — a best-effort read of an
unknown shape is how a tool ends up confidently reporting the wrong thing, which
costs more than reporting nothing. The field set and its stability tiers are
written down at
https://github.com/hancheng-ai/nextbrief/blob/main/docs/INVENTORY_SCHEMA.md.

The envelope is `schema_version`, `generated_at`, `root`, and `projects`. Each
project entry carries at least `id`, `name`, `path`, `description`, `goal`,
`stacks`, `run`, `needs`, `unlocks` and `serves`. A `description` is an object,
not a string: read `description.kind` before `description.what`, because
`declared` (the owner wrote it), `observed` (lifted out of a manifest) and
`absent` (we looked and found nothing) are three different warrants, and
`absent` is not the same as a missing key.

## The rest of what you can read

Same data, shaped for a person rather than a parser:

```bash
nextbrief projects
```

Today's brief — what actually moved, with every claim already checked against
evidence before it was allowed to print:

```bash
nextbrief brief
```

What is open across the portfolio, and one item in full:

```bash
nextbrief ls
nextbrief show <id>
```

What was closed recently, optionally for one project. Read this before
proposing anything: the most common wasted suggestion is one that was finished
last week, and the second most common is one that was deliberately dropped.

```bash
nextbrief closed
nextbrief closed <project>
```

## Reading the answers

- **Exit `2` means no workspace could be resolved.** The workspace comes from
  `$NEXTBRIEF_WORKSPACE`, the pointer written when it was set up, or the nearest
  `registry.jsonc`. Say that it is not set up rather than guessing a path.
- **Exit `1` from the inventory means it has not been generated yet.** Report
  that; do not generate it yourself.
- **`needs` and `unlocks` are the useful pair.** Before building something,
  check whether another project in the list already provides it.
- **A project's declared non-goals are decisions, not gaps.** Something absent
  from a portfolio on purpose looks identical to something nobody got to, and
  only the record can tell you which.

## What this skill will not do for you

Closing an item, parking it, or starting a working session on one are the
owner's actions, and they are deliberately outside this skill. A false
completion is far more expensive than a missed one: the missed one comes back
tomorrow and the false one never does.

If your work finishes something on the list, **say which item and let the owner
close it.** Reporting a finished item is useful. Recording it yourself is not
yours to do.
