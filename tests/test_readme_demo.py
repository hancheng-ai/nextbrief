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


def fenced_block(text: str, lang: str, must_contain: str) -> str:
    """The first ```lang fenced block containing `must_contain`, without fences."""
    for m in re.finditer(r"```%s\n(.*?)\n```" % lang, text, re.DOTALL):
        if must_contain in m.group(1):
            return m.group(1)
    raise AssertionError("no ```%s block containing %r" % (lang, must_contain))


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


class ReadmeDemoReproduces(TempCase):
    """Run what the README tells the reader to run, then diff the two outputs."""

    @requires_git
    def test_rejected_jsonl_matches_both_readmes(self):
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
        self.assertIn("4 unverifiable claim(s) dropped", out)

        produced = (ws / "log" / "rejected.jsonl").read_text(encoding="utf-8").strip()
        self.assertEqual(len(read_jsonl(ws / "log" / "rejected.jsonl")), 4)

        brief_md = (ws / "BRIEF.md").read_text(encoding="utf-8").rstrip("\n")

        for name in ("README.md", "README.zh.md"):
            doc = (REPO_ROOT / name).read_text(encoding="utf-8")

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
            self.assertIn(
                "| %d lines |" % len(brief_md.splitlines()), console,
                "%s: the transcript's line count is stale" % name,
            )

            self.assertEqual(
                fenced_block(doc, "jsonl", "unresolvable_evidence"),
                produced,
                "%s prints a log/rejected.jsonl that the documented commands no "
                "longer produce" % name,
            )
            self.assertEqual(
                fenced_block(doc, "markdown", "# Daily brief"),
                brief_md,
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


if __name__ == "__main__":
    unittest.main()
