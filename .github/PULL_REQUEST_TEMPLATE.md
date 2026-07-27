<!--
Thanks for sending this. Small and focused gets merged fast; the four extension
points (providers, sinks, locales, parsers) are the easiest of all to review.
See CONTRIBUTING.md.
-->

## What this changes

<!-- One or two sentences. If it fixes an issue, "Fixes #123". -->

## Why

<!--
The reasoning, not the diff -- the diff is right there. If you made a tradeoff,
this is where it goes, and it is also what belongs in the code comment.
-->

## Checklist

- [ ] `python -m unittest discover -s tests -v` passes
- [ ] `ruff check .` is clean
- [ ] **No new runtime dependency.** Stdlib only, Python 3.9 compatible: no
      `match`, no `X | Y` at runtime, no 3.10+ APIs
- [ ] No real names, hosts, absolute home paths, or workspace contents anywhere
      in the diff -- examples and fixtures are fictional (CI enforces this)
- [ ] Comments explain *why*, in English

## Design contract

Tick the ones your change touches, or write "n/a" if it touches none.

- [ ] **sense and render stay deterministic** -- same input, byte-identical
      output apart from timestamps; nothing sorts or groups by wall clock
- [ ] **the model only interprets** -- no decision moved out of Python and into
      the prompt
- [ ] **every rendered claim resolves against the snapshot** -- unverifiable
      claims are dropped to `log/rejected.jsonl`, never printed
- [ ] **no agent sets a terminal status** -- `done` and `dropped` remain a
      human's to write
- [ ] **nothing is written outside the workspace directory**
- [ ] **fail-open** -- a failing parser returns `None` and records the path; it
      does not raise through the pipeline

<!--
If your change strains one of these, say so explicitly instead of ticking the
box. A well-argued exception is a conversation. A quietly ticked box is not.
-->
