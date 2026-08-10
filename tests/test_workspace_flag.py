"""Which subcommands honour the global ``--workspace``, written down and pinned.

``init`` was fixed on its own once it turned out to accept the flag and scaffold
a workspace somewhere else entirely. Nothing then established what the other
twenty-four do with it, and "the one we looked at was wrong" is a poor place to
stop -- so this file is the survey, and the survey is the fixture.

The answer today is uniform, which is worth stating plainly because it is the
thing a future change would break rather than something to be relieved about:

``cli.main`` resolves the workspace **once**, before dispatch, and hands the
resulting ``Workspace`` to the handler. Every command in ``_HANDLERS`` therefore
honours the flag by construction; ``init`` is dispatched before that line,
because it creates a workspace rather than operating on one, and refuses the
flag rather than reinterpreting it.

Two details that the uniform answer hides, both load-bearing:

* **The four pipeline commands honour it at one remove.** ``sense`` and
  ``render`` are separate modules with ``--workspace`` flags of their own, and
  ``_stage_args`` strips the global one out of what gets forwarded to them. What
  reaches them is ``$NEXTBRIEF_WORKSPACE``, exported by ``_export_env`` from the
  already-resolved workspace. So the flag works, but through the environment --
  and a stage invoked by some future path that does not go through
  ``_export_env`` would silently fall back to discovering one from the cwd.

* **Resolving before dispatch is what makes the refusal test below uniform.**
  Point the flag at a directory that is not a workspace and every command fails
  identically, having done nothing. That proves ``main`` *consults* the flag; it
  says nothing about whether the handler then uses the workspace it was handed,
  which is why the second test runs from inside a *different* valid workspace and
  checks which one the output speaks of.

Determined by reading the resolution path and by running each command against
two throwaway workspaces. `prune` is the one whose behaviour could not be
separated that way -- its output is identical in a workspace with nothing to
prune and a workspace with something, once the "project has shown no evidence"
condition is applied -- so its entry rests on the reading and on the refusal.
"""

from __future__ import annotations

import json
import os
import unittest

from helpers import TempCase, capture, make_workspace, write_backlog_item

from nextbrief import cli

HONOURED = "honoured"
REFUSED = "refused"

# The survey. A command may not be in `_HANDLERS` and absent from here, which is
# what stops the next subcommand joining without anyone deciding what the flag
# means for it.
SURVEY = {
    "run":         (HONOURED, "resolved in main, then reaches sense/render via $NEXTBRIEF_WORKSPACE"),
    "v0":          (HONOURED, "same, minus the model"),
    "sense":       (HONOURED, "own --workspace flag; the global one arrives as $NEXTBRIEF_WORKSPACE"),
    "render":      (HONOURED, "same as sense"),
    "check":       (HONOURED, "ws.snapshot / ws.brief_md"),
    "open":        (HONOURED, "ws.brief_html"),
    "brief":       (HONOURED, "ws.brief_md"),
    "log":         (HONOURED, "ws.log"),
    "new":         (HONOURED, "counts the ids under ws.backlog, and writes the file there"),
    "do":          (HONOURED, "finds the item under ws.backlog before it launches anything"),
    "show":        (HONOURED, "ws.backlog"),
    "ok":          (HONOURED, "ws.backlog"),
    "done":        (HONOURED, "ws.backlog + ws.log"),
    "drop":        (HONOURED, "ws.backlog"),
    "defer":       (HONOURED, "ws.backlog + ws.log"),
    "followup":    (HONOURED, "ws.backlog"),
    "closed":      (HONOURED, "ws.backlog"),
    "ls":          (HONOURED, "ws.backlog"),
    "prune":       (HONOURED, "_decay_candidates(ws, ...); refusal-tested only, see the module docstring"),
    "probe":       (HONOURED, "ws.registry_path decides what is fetched, so this one matters most"),
    "projects":    (HONOURED, "ws.snapshot"),
    "context":     (HONOURED, "ws inventory"),
    "describe":    (HONOURED, "ws.registry_path"),
    "review":      (HONOURED, "ws.snapshot"),
    "permissions": (HONOURED, "prints rules naming ws.root"),
    "init":        (REFUSED,  "creates a workspace rather than operating on one; see _INIT_REFUSES"),
}

# Enough argv for argparse to accept the command. The refusal happens during
# workspace resolution, before any of these mean anything.
NEEDS_ID = ("do", "show", "ok", "drop", "done", "defer", "followup")

# Commands whose own arguments are not an id. Spelled out rather than folded
# into the line above, because getting one of these wrong turns a refusal test
# into an argparse usage error -- which also exits non-zero and would be read as
# the command having stopped at the flag.
EXTRA_ARGV = {"new": ["a title", "--project", "orchard"]}


def _argv_for(command):
    if command in EXTRA_ARGV:
        return [command] + EXTRA_ARGV[command]
    return [command] + (["XX-0001"] if command in NEEDS_ID else [])


def _subcommands_of_parser():
    """The subcommand names argparse actually exposes, read off the parser.

    Taken from the parser rather than from `_HANDLERS` so that a subcommand
    someone adds to `build_parser` and forgets to wire up is caught here too --
    it would otherwise be a command that exists, parses, and does nothing.
    """
    for action in cli.build_parser()._actions:
        if getattr(action, "choices", None) and hasattr(action, "_name_parser_map"):
            return set(action._name_parser_map)
    raise AssertionError("build_parser() exposes no subparsers")


class TheSurveyIsComplete(unittest.TestCase):
    def test_every_subcommand_has_a_verdict(self):
        exposed = _subcommands_of_parser()
        self.assertGreater(len(exposed), 20, "found %d subcommands; the parser was "
                                             "not read correctly" % len(exposed))
        self.assertEqual(
            exposed, set(SURVEY),
            "the survey and the parser disagree. Missing a verdict: %s. "
            "Surveyed but gone: %s"
            % (sorted(exposed - set(SURVEY)), sorted(set(SURVEY) - exposed)))

    def test_everything_with_a_handler_is_surveyed_as_honouring_it(self):
        """`_HANDLERS` is reached only after the workspace is resolved, so
        membership there *is* the honouring. A command surveyed as refusing the
        flag while sitting in that table would be a contradiction."""
        for name in cli._HANDLERS:
            self.assertEqual(HONOURED, SURVEY[name][0],
                             "%s is in _HANDLERS, which is downstream of "
                             "resolve_workspace" % name)
        refused = {n for n, (verdict, _) in SURVEY.items() if verdict == REFUSED}
        self.assertEqual({"init"}, refused,
                         "only init is dispatched before the workspace is resolved")

    def test_the_stages_are_handed_it_through_the_environment(self):
        """The one place the flag travels as something other than a flag.

        `_stage_args` removes `--workspace` from what is forwarded, so `sense`
        and `render` never see it; `_export_env` is what makes them agree with
        the rest. Pinned because the two halves are in different functions and
        deleting either leaves the other looking correct.
        """
        self.assertIn("--workspace", cli._GLOBAL_FLAGS)
        self.assertEqual(
            ["--stdout"],
            cli._stage_args(["--workspace", "/somewhere", "sense", "--stdout"], "sense"))
        self.assertEqual(
            ["--stdout"],
            cli._stage_args(["sense", "--workspace", "/somewhere", "--stdout"], "sense"))


class PointedAtSomethingThatIsNotAWorkspace(TempCase):
    """Every command must stop, and stop for the same reason.

    Run from inside a perfectly good workspace, so a command that ignored the
    flag would succeed rather than fail -- the failure is the evidence.
    """

    def setUp(self):
        super().setUp()
        self.good = make_workspace(self.tmp / "good", with_git=False)
        self.hollow = self.tmp / "hollow"
        self.hollow.mkdir()
        os.chdir(str(self.good))

    def test_every_command_that_honours_it_refuses_a_hollow_directory(self):
        """All of them at once, rather than stopping at the first.

        Every command is reported, because "one of them regressed" and "the
        resolver regressed for all of them" look identical when the loop stops
        on the first offender, and they call for completely different fixes.
        """
        wrong, checked = [], 0
        for name, (verdict, _note) in sorted(SURVEY.items()):
            if verdict != HONOURED:
                continue
            checked += 1
            code, out, err = capture(
                cli.main, ["--workspace", str(self.hollow)] + _argv_for(name))
            text = (out + err).strip().replace("\n", " ")
            if code == 0 or "has no registry.jsonc" not in text:
                wrong.append("%s (exit %s): %s" % (name, code, text[:160]))
        self.assertEqual(
            [], wrong,
            "these commands did not stop at the flag -- they read the cwd, or "
            "failed for a reason that is not the flag:\n%s" % "\n".join("  " + w for w in wrong))
        self.assertEqual(checked, sum(1 for v, _ in SURVEY.values() if v == HONOURED))
        self.assertGreater(checked, 20, "only %d commands were exercised" % checked)

    def test_init_refuses_the_flag_by_name(self):
        code, out, err = capture(
            cli.main, ["--workspace", str(self.hollow), "init", str(self.tmp / "new"), "-y",
                       "--no-scan"])
        self.assertNotEqual(0, code, "init accepted --workspace instead of refusing it")
        self.assertIn("--workspace cannot be used with init", out + err)
        self.assertFalse((self.tmp / "new").exists(),
                         "init was refused and created the workspace anyway")


class TheFlagBeatsTheWorkspaceYouAreStandingIn(TempCase):
    """The half the refusal test cannot reach: does the *handler* use it?

    Both directories are valid workspaces holding different things, and the cwd
    is the wrong one. A handler that re-derived a workspace from the cwd would
    produce a clean, plausible, entirely wrong answer -- which is the failure
    worth catching, because nothing about it looks like a failure.
    """

    def setUp(self):
        super().setUp()
        self.here = self._build("here", "AA-0001")
        self.there = self._build("there", "BB-0001")
        os.chdir(str(self.here))

    def _build(self, name, item_id):
        root = make_workspace(self.tmp / name, with_git=False)
        write_backlog_item(root, item_id, title="%s item" % name, project="orchard")
        (root / "BRIEF.md").write_text("# Brief\n\nmarker-%s\n" % name, encoding="utf-8")
        (root / "log").mkdir(exist_ok=True)
        (root / "log" / "runs.jsonl").write_text(
            json.dumps({"at": "2026-03-16T09:00:00+00:00", "stage": "render",
                        "note": "runlog-%s" % name}) + "\n", encoding="utf-8")
        return root

    def _there(self, *argv):
        return capture(cli.main, ["--workspace", str(self.there)] + list(argv))

    def test_ls_lists_the_other_workspaces_items(self):
        code, out, err = self._there("ls")
        self.assertEqual(0, code, err)
        self.assertIn("BB-0001", out)
        self.assertNotIn("AA-0001", out)

    def test_show_cannot_find_the_item_that_is_only_here(self):
        """The two-sided half. `show BB-0001` succeeding proves it read *a*
        workspace; `show AA-0001` failing proves it was not this one."""
        code, out, err = self._there("show", "BB-0001")
        self.assertEqual(0, code, "the flag named the workspace BB-0001 is in, "
                                  "and it was not found there: %s" % (out + err))
        code, out, err = self._there("show", "AA-0001")
        self.assertNotEqual(0, code, "AA-0001 is only in the cwd's workspace, so "
                                     "finding it means the flag lost")
        self.assertIn("No item AA-0001", out + err)

    def test_brief_prints_the_other_workspaces_brief(self):
        code, out, err = self._there("brief")
        self.assertEqual(0, code, err)
        self.assertIn("marker-there", out)
        self.assertNotIn("marker-here", out)

    def test_log_reads_the_other_workspaces_log(self):
        code, out, err = self._there("log")
        self.assertEqual(0, code, err)
        self.assertIn("runlog-there", out + err)
        self.assertNotIn("runlog-here", out + err)

    def test_permissions_writes_rules_for_the_other_workspace(self):
        code, out, err = self._there("permissions")
        self.assertEqual(0, code, err)
        # Posix form, because that is the form the rule is written in. The two
        # spellings are the same string on this host, so `str()` would pass here
        # and assert against a path the rule never contained on Windows.
        self.assertIn(self.there.as_posix(), out)
        self.assertNotIn(self.here.as_posix(), out)


if __name__ == "__main__":
    unittest.main()
