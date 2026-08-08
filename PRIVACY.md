# Privacy

nextbrief is a command-line program that runs on your machine. There is no
server, no account, no telemetry, no analytics and no update check. Nothing is
collected about you, because there is nowhere for it to be collected to.

That is not the same as "nothing leaves your machine", and the difference is the
part worth reading.

## Two things do go out, and here is exactly what

**Stage 2 sends a digest to the model you configured.** `nextbrief run` builds
`state/digest.json` and passes it to whichever provider your `config.jsonc`
names, using your own credentials or your own already-logged-in CLI. It contains
what your registry declares (project ids, names, goals, notes, non-goals quoted
verbatim), what was derived from the filesystem and git (dates, counts, days
since anything moved, the subject line of the last commit, and the paths of
changed and status files), and a summary of your backlog items. It does not
contain the contents of your source files. It does contain **file and directory
paths, commit subjects, and prose you wrote** — so if a path or a heading is
itself sensitive, treat it as leaving.

It goes to your provider, under that provider's terms and retention, not
through anything the author operates. Which provider, and whether you use one at
all, is your choice: **`nextbrief v0` runs stages 1 and 3 and sends nothing.**

**`nextbrief probe` fetches URLs, and only when you type it.** It is opt-in per
run and per project: it issues an HTTPS GET against URLs your own
`registry.jsonc` declares under `evidence_probe`, with no credentials and no
cookies, and refuses a redirect that leaves the declared origin. It is not part
of `run`, and the scheduled nightly job never calls it. Nothing chooses those
URLs but you.

Everything else is local. The nightly pipeline opens no network connection
except the model call.

## What it reads, and what you can put out of reach

It reads the directories your `registry.jsonc` lists — names, sizes,
modification times, `git log`, and the Markdown you already keep — and agent
session transcripts if you configure that. It writes only inside the workspace
you chose.

`privacy.never_read` in a project entry marks paths that are never opened. Those
contribute exactly one thing: an integer count. Not the contents, and **not the
filenames**, because the name is often the sensitive part. Stage 1 refuses to
write a snapshot that contains a covered path at all, so nothing about them can
reach the model or the page.

What it does **not** cover, stated plainly because a control you misread is
worse than no control:

- **It is a path filter, not a content filter.** A credential pasted into a
  Markdown file that *is* read is read like any other text.
- **It only covers what you marked.** Anything else inside a directory your
  registry lists is in scope.
- **It is not retroactive.** Adding a rule does not rewrite a snapshot, digest,
  brief or log already on disk, and does not reach anything already sent.
- **It has nothing to do with `probe`.** Those URLs are governed by your
  registry's `evidence_probe` entries alone.

## Data we hold

None. There is no service to write to, no account to close, and nothing on the
other end to ask. Everything nextbrief produces is in the workspace directory
you created; removing that directory removes all of it.

This page deliberately promises nothing beyond that — no retention window, no
erasure procedure, no list of processors. Each of those would need an operator
on the other end, and there isn't one.

## More detail

[SECURITY.md](SECURITY.md) is the longer version — the constraints on `probe`
and the tests that hold them, why content read out of your projects is treated
as untrusted input, and how to report a vulnerability. This file is a summary of
that one and defers to it wherever they could be read differently.
