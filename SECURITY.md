# Security

## Reporting a vulnerability

Open a [security advisory](https://github.com/hancheng-ai/nextbrief/security/advisories/new)
rather than a public issue. If that is not available to you, open an issue saying
only that you have something to report and asking for a channel — no details.

Expect a first reply within a week. This is a small project maintained by one
person; a fix will not always be fast, but a "yes, that is real" will be.

## What this software can reach

nextbrief reads every directory its registry lists. That is the whole point of it
and also its largest attack surface, so this section says exactly what it does.

**It reads.** File names, sizes and modification times; `git log` output; the
Markdown files you already keep; and, since 0.1.0, agent session transcripts
under `~/.claude/projects` when configured. Read-only.

**It writes only inside the workspace you chose** — `state/`, `log/`, `BRIEF.md`,
`BRIEF.html`. It never writes to a project directory, and it never edits
`registry.jsonc` or `config.jsonc`: those are yours, comments and ordering
included, and a tool that rewrites them will eventually get that wrong on the one
file whose loss costs most.

**It sends one file to one place.** Stage 2 passes `state/digest.json` to the
model you configured. Nothing else leaves the machine — no telemetry, no
analytics, no update check. `nextbrief v0` runs stages 1 and 3 only and sends
nothing at all.

**It has no runtime dependencies.** Nothing to audit but this repository and the
standard library.

## The trust boundary that matters most

**Content read out of your projects is data to report, never a command to
follow.** A file in a directory nextbrief scans is untrusted input, exactly like
a web page: it may contain text addressed to a model, and that text has no
authority.

This is enforced rather than requested. The evidence gate in stage 3 resolves
every claim the model makes against `state/snapshot.json`, which stage 2 never
sees; a claim whose source does not resolve is not rendered at all. So a
malicious file cannot cause a fabricated statement to reach the page, because the
page only prints statements whose evidence checks out in a stage with no model in
it.

The example workspace ships a file that attempts this
(`handoff-inbox/vendor-notes.md`, which instructs the reader to mark every task
complete), and a test asserts the instruction is quoted rather than obeyed. If
you find a path that gets around it, that is a vulnerability and worth reporting.

## Paths you can put out of reach

`privacy.never_read` in the registry marks paths that are never opened. For
those, stage 1 records a single integer count — not the contents, and **not the
filenames either**, because a filename is often the sensitive part. Nothing about
them reaches the snapshot, so nothing about them can reach the model or the page.

## What is not a vulnerability

- **A brief that is wrong.** The engine reports what it observed; observations
  can be misleading. A *fabricated* claim that passed the evidence gate is a
  vulnerability. An unhelpful but supported one is a bug.
- **Reading a file you pointed it at.** Scope is what the registry says. If it
  read something you did not expect, check what your registry lists — and if the
  registry did not list it, that is worth reporting.
- **`nextbrief do` opening a session in a project directory.** That is the
  command's purpose, and it asks first.

## Supported versions

The latest release only. This is a prerelease project; there is no backport
branch, and pinning an old version means keeping it yourself.
