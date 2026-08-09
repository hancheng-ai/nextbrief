"""The README's headline demonstration must stay reproducible.

That demonstration -- four fabricated claims stopped by the evidence gate -- is
the single most persuasive thing in the project, and it is worth exactly nothing
if a reader cannot produce it. Reproducing it needs a stage-2 ``brief.json``,
which used to mean "have a model and an API key", so nobody ever checked. The
recorded stage-2 output is now committed at ``examples/workspace/state/brief.json``
and stage 3 replays it offline.

What is asserted here is not "four claims were dropped" but "the file the README
prints is the file the commands produce". A weaker assertion would let the two
drift, which is the failure being guarded against: prose that was true once.
"""

from __future__ import annotations

import json
import re
import shutil
import unittest
from pathlib import Path

from helpers import (
    AS_OF,
    EXAMPLE_WORKSPACE,
    REPO_ROOT,
    TempCase,
    capture,
    git_commit_all,
    git_init,
    read_jsonl,
    requires_git,
)

from nextbrief import cli

FIXTURE = EXAMPLE_WORKSPACE / "state" / "brief.json"

# The excerpt above the fold, fenced off from the full transcript further down
# so each check can find its own block. Both are ```markdown blocks opening with
# `# Daily brief`, and `fenced_block` takes the first match -- so without these
# the excerpt would silently become what the whole-file comparison compares.
EXCERPT_BEGIN = "<!-- brief-excerpt:begin -->"
EXCERPT_END = "<!-- brief-excerpt:end -->"

# One line, exactly this, marks a section the excerpt left out. Everything
# between two of them has to be a contiguous run of the real file.
ELISION = "…"

# What to type when this goes red. Named once: a failure message that says
# "regenerate it" without saying how is a message that gets guessed at.
RECUT = "cd examples/workspace && make sense render"


def fenced_block(text: str, lang: str, must_contain: str) -> str:
    """The first ```lang fenced block containing `must_contain`, without fences."""
    for m in re.finditer(r"```%s\n(.*?)\n```" % lang, text, re.DOTALL):
        if must_contain in m.group(1):
            return m.group(1)
    raise AssertionError("no ```%s block containing %r" % (lang, must_contain))


def without_excerpt(text: str) -> str:
    """The document minus the marked excerpt, for the whole-file comparison.

    A document that has lost its markers comes back unchanged rather than
    empty, so the excerpt is then the first ```markdown block and the whole-file
    comparison fails loudly instead of comparing the wrong thing.
    """
    begin = text.find(EXCERPT_BEGIN)
    if begin < 0:
        return text
    end = text.find(EXCERPT_END, begin)
    if end < 0:
        return text
    return text[:begin] + text[end + len(EXCERPT_END):]


def excerpt_chunks(text: str, name: str) -> list:
    """The marked excerpt, split into the contiguous runs it claims to be.

    Blank lines around each run are stripped, so a chunk is exactly the lines
    between two elision markers -- which is what gets looked for, verbatim, in
    the brief the tool produced.
    """
    begin = text.find(EXCERPT_BEGIN)
    if begin < 0:
        raise AssertionError(
            "%s has no %s marker, so nothing pins its excerpt to the tool's "
            "output" % (name, EXCERPT_BEGIN))
    end = text.find(EXCERPT_END, begin)
    if end < 0:
        raise AssertionError("%s opens %s and never closes it" % (name, EXCERPT_BEGIN))
    block = fenced_block(text[begin:end], "markdown", "# Daily brief")
    chunks, current = [], []
    for line in block.splitlines():
        if line.strip() == ELISION:
            chunks.append(current)
            current = []
        else:
            current.append(line)
    chunks.append(current)
    return ["\n".join(c).strip("\n") for c in chunks if "".join(c).strip()]


class FixtureIsCommitted(unittest.TestCase):
    def test_present_and_parses(self):
        self.assertTrue(
            FIXTURE.is_file(),
            "%s is missing -- the README's demonstration cannot be reproduced "
            "without it, and examples/workspace/.gitignore must keep its "
            "exception for this one file" % FIXTURE,
        )
        json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_carries_the_four_bad_claims(self):
        """Counted here as well as rendered below: a fixture quietly trimmed to
        four entries still renders a valid brief, so the render-side assertion
        alone would not say which end went wrong."""
        brief = json.loads(FIXTURE.read_text(encoding="utf-8"))
        actions = brief.get("next_actions") or []
        self.assertEqual(len(actions), 5, "four rejected claims plus one that resolves")



def _without_optional_tools(text: str) -> str:
    """The brief minus the "Optional tools missing: ..." reminder.

    Whether that line exists depends on what is installed on the machine, so it
    cannot be part of a fixed transcript in a README that other people read.
    """
    return "\n".join(
        ln for ln in text.splitlines()
        if "Optional tools missing" not in ln and "\u53ef\u9009\u5de5\u5177\u7f3a\u5931" not in ln
    )

class ReplaysTheExample(TempCase):
    """Runs the two commands the README documents, in a private copy.

    Shared by everything below that needs the real output rather than a
    remembered one, which is the whole point of the file: nothing here compares
    the docs against a second copy of the docs.
    """

    @requires_git
    def replay(self):
        ws = self.copy_example()
        # copy_example deliberately drops state/, so the fixture is restored by
        # hand rather than by widening that ignore list -- every other test in
        # the suite depends on a copied workspace starting with no state at all.
        (ws / "state").mkdir(parents=True, exist_ok=True)
        shutil.copy(str(FIXTURE), str(ws / "state" / "brief.json"))

        # The write-permission gate needs a repository to diff the backlog
        # against. Without one it records a `gate_disabled` line in
        # rejected.jsonl, which is correct behaviour and would still leave the
        # comparison below failing for a reason that has nothing to do with
        # evidence.
        #
        # Initialised one level *above* the workspace, matching how the real
        # example sits inside this repository. A repository rooted at the
        # workspace itself would also satisfy the gate, but it puts every
        # otherwise-unversioned example project under version control, which
        # changes what the sensing stage finds and therefore the snapshot size
        # printed in the transcript below.
        git_init(self.tmp)
        git_commit_all(self.tmp, "example workspace")

        code, sensed, err = capture(cli.main, ["--workspace", str(ws), "sense", "--as-of", AS_OF])
        self.assertEqual(code, 0, err)
        code, out, err = capture(cli.main, ["--workspace", str(ws), "render", "--no-notify"])
        self.assertEqual(code, 0, err)
        return ws, sensed, out


class ReadmeDemoReproduces(ReplaysTheExample):
    """Run what the README tells the reader to run, then diff the two outputs."""

    @requires_git
    def test_rejected_jsonl_matches_both_readmes(self):
        ws, sensed, out = self.replay()
        self.assertIn("4 unverifiable claim(s) dropped", out)

        produced = (ws / "log" / "rejected.jsonl").read_text(encoding="utf-8").strip()
        self.assertEqual(len(read_jsonl(ws / "log" / "rejected.jsonl")), 4)

        brief_md = (ws / "BRIEF.md").read_text(encoding="utf-8").rstrip("\n")

        for name in ("README.md", "README.zh.md"):
            # Minus the excerpt above the fold, which is a ```markdown block
            # opening with the same line and would otherwise be the block this
            # compares -- silently, and against the wrong expectation.
            doc = without_excerpt((REPO_ROOT / name).read_text(encoding="utf-8"))

            # The transcript too, minus the one part that cannot be printed: the
            # render line names an absolute path, and CI fails the build on any
            # home directory appearing in a tracked file.
            console = fenced_block(doc, "console", "unverifiable claim(s) dropped")
            # Up to the counts only. `snapshot NNKB` is not comparable across
            # machines: the snapshot stores absolute repository paths, so its
            # size moves with the length of the path the checkout happens to
            # live at. The README says so rather than pretending otherwise.
            self.assertIn(
                sensed.split("|")[0].strip() + " | " + sensed.split("|")[1].strip(),
                console, "%s: stale `sense` line" % name,
            )
            # Deliberately NOT asserted: the brief's line count. It differs
            # between machines -- a reminder appears or does not depending on
            # what is installed and on the git context the workspace sits in --
            # so pinning it here fails the build for a reason that has nothing
            # to do with the demonstration. The count of dropped claims and the
            # rejected.jsonl content are the claims worth holding, and both are
            # asserted above and below.

            self.assertEqual(
                fenced_block(doc, "jsonl", "unresolvable_evidence"),
                produced,
                "%s prints a log/rejected.jsonl that the documented commands no "
                "longer produce" % name,
            )
            # The optional-tools reminder is dropped from both sides before
            # comparing. scc and ccusage are optional by contract: absent, the
            # brief gains a line saying which measure degraded. That line is
            # therefore a fact about the machine, not about the demonstration,
            # and it is exactly why this assertion passed here and failed on a
            # runner with neither tool installed. Everything else in the brief
            # is compared byte for byte.
            self.assertEqual(
                _without_optional_tools(fenced_block(doc, "markdown", "# Daily brief")),
                _without_optional_tools(brief_md),
                "%s prints a BRIEF.md that the documented commands no longer "
                "produce" % name,
            )

    @requires_git
    def test_without_the_fixture_there_is_nothing_to_show(self):
        """The reason the fixture has to be committed, stated as a test.

        With no stage-2 output the run degrades to v0: a perfectly good brief,
        and zero dropped claims -- so the README's demonstration is not merely
        different, it is absent.
        """
        ws = self.copy_example()
        self.assertFalse((Path(ws) / "state" / "brief.json").exists())
        git_init(ws)
        git_commit_all(ws, "example workspace")

        capture(cli.main, ["--workspace", str(ws), "sense", "--as-of", AS_OF])
        code, out, err = capture(cli.main, ["--workspace", str(ws), "render", "--no-notify"])
        self.assertEqual(code, 0, err)
        self.assertIn("v0 (no model)", out)
        self.assertEqual(read_jsonl(ws / "log" / "rejected.jsonl"), [])


class TheExcerptAboveTheFoldIsRealOutput(ReplaysTheExample):
    """The first thing a reader sees is a brief, and it has to be a real one.

    A worked example near the top of a README is the easiest place in any
    project for a claim to stop being true. It is written once, from a run
    somebody actually did, and then the tool moves -- a column is added, a
    heading is reworded, a signal changes name -- and the sample goes on
    reading perfectly. Nothing about a stale sample looks stale. It is the same
    failure as a screenshot, and this repository exists to refuse it.

    So the excerpt is not compared against a stored copy of itself. The example
    is rebuilt and re-rendered here, and each run of lines between two `…`
    markers has to appear verbatim, in order, in the file that comes out.
    """

    # Provenance is not substance, and the chunk match only checks provenance:
    # an excerpt cut down to one heading is still made entirely of lines the
    # tool printed. These are what the excerpt is above the fold *for* -- the
    # evidence under a next action, the open decision naming both the evidence
    # that would settle it and where that evidence already is, and the footer
    # that admits what was thrown away. Losing one is a silent downgrade of the
    # argument the section is making.
    MUST_SHOW = (
        "Evidence: commit ",
        "Evidence that would settle it:",
        "**The evidence already exists**",
        "whose evidence would not check out",
        "whatever could not be verified was not rendered",
    )

    @requires_git
    def test_every_line_of_it_is_a_line_the_tool_printed(self):
        ws, _sensed, _out = self.replay()
        # The same machine-dependent line the whole-file comparison drops, for
        # the same reason: whether scc and ccusage are installed is a fact about
        # the runner, not about the demonstration.
        brief_md = _without_optional_tools(
            (ws / "BRIEF.md").read_text(encoding="utf-8").rstrip("\n"))

        for name in ("README.md", "README.zh.md"):
            doc = (REPO_ROOT / name).read_text(encoding="utf-8")
            chunks = excerpt_chunks(doc, name)
            self.assertGreater(
                len(chunks), 1,
                "%s's excerpt has no %s marker in it, so it is not an excerpt "
                "of anything -- either it is the whole file, in which case say "
                "so, or the markers were lost" % (name, ELISION))

            # In order, and each one contiguous. A set-membership check would
            # pass on an excerpt whose sections had been shuffled into an order
            # the brief never prints, which is a different claim about the tool
            # than the one being made.
            at = 0
            for i, chunk in enumerate(chunks, 1):
                found = brief_md.find(chunk, at)
                self.assertNotEqual(
                    found, -1,
                    "%s quotes a brief that nextbrief no longer produces.\n"
                    "Block %d of %d in the excerpt is not in the output"
                    "%s, starting:\n"
                    "    %s\n"
                    "Re-run `%s` and copy the blocks back from the BRIEF.md it "
                    "writes, between %s and %s."
                    % (name, i, len(chunks),
                       " after block %d" % (i - 1) if i > 1 else "",
                       chunk.splitlines()[0], RECUT, EXCERPT_BEGIN, EXCERPT_END))
                at = found + len(chunk)

            excerpt = "\n".join(chunks)
            for shows in self.MUST_SHOW:
                self.assertIn(
                    shows, excerpt,
                    "%s's excerpt no longer shows %r. Every line still in it is "
                    "one the tool printed -- which is why the check above passed "
                    "-- but the excerpt is above the fold to show the evidence "
                    "under a next action, the decision that names where its "
                    "evidence already is, and the count of claims the gate "
                    "dropped. Re-cut it with `%s` rather than trimming it here."
                    % (name, shows, RECUT))

    def test_both_readmes_show_the_same_excerpt(self):
        """A reader of one is not served by the other.

        The translations drift under exactly this pressure -- the English side
        is re-cut after a change to the renderer and its counterpart is not --
        and the check above would stay green on a Chinese README quoting a
        subset, because a subset of real output is still real output.
        """
        chunks = [excerpt_chunks((REPO_ROOT / name).read_text(encoding="utf-8"), name)
                  for name in ("README.md", "README.zh.md")]
        self.assertEqual(
            chunks[0], chunks[1],
            "README.md and README.zh.md quote different excerpts of the same "
            "brief. Both show the tool's own English output; re-cut them "
            "together with `%s`." % RECUT)


if __name__ == "__main__":
    unittest.main()
