"""What ``nextbrief init`` is allowed to put in a stranger's workspace.

The registry template doubles as the registry's documentation, so it ships six
worked examples -- Orchard API, Beacon Portal, Kiln and friends. Those examples
once survived into every scaffolded workspace, and the first brief a new user
saw therefore reported six projects, one pending decision and a deadline months
overdue, under a footer asserting that every claim had passed the evidence gate.
None of it was true of anybody. That is the exact failure this project exists to
prevent, so the rule is checked here from several directions at once: through the
CLI, through ``render_registry`` directly, and through the JSON fallback that
takes over when the textual substitution cannot be made.
"""

from __future__ import annotations

import json
import re
import unittest

from helpers import TempCase, capture

from nextbrief import cli, resources
from nextbrief import init as init_mod
from nextbrief.jsonc import load_jsonc, loads_jsonc
from nextbrief.paths import pointer_file

# Ids the packaged template carries as documentation. Not one of them may reach a
# workspace, whatever the flags.
DEMO_IDS = ("orchard-api", "beacon-portal", "lantern-site", "tidepool-docs", "kiln", "quarry")


def registry_text() -> str:
    text = resources.read_text("templates", "registry.example.jsonc")
    assert text is not None, "packaged registry template is missing"
    return text


class ScaffoldedRegistry(TempCase):
    """BLOCKER A -- the scaffolded registry may contain only what was discovered."""

    def setUp(self):
        super().setUp()
        # The workspace sits one level down so its parent is empty: discovery has
        # nothing local to wander into, which is the state a fresh install is in.
        self.box = self.tmp / "box"
        self.box.mkdir()
        self.target = self.box / "ws"

    def _init(self, *flags):
        return capture(cli.main, ["init", str(self.target), *flags])

    def _projects(self):
        return load_jsonc(self.target / "registry.jsonc")["projects"]

    def _assert_no_demo_content(self):
        raw = (self.target / "registry.jsonc").read_text(encoding="utf-8")
        data = loads_jsonc(raw)
        for demo in DEMO_IDS:
            self.assertNotIn(
                demo,
                json.dumps(data, ensure_ascii=False),
                "%r from the packaged template survived into the workspace" % demo,
            )
        for key in init_mod.DEMO_LISTS:
            self.assertFalse(data.get(key), "%s still carries template examples" % key)

    def test_no_flag_combination_adopts_the_templates_worked_examples(self):
        # Each of these is a way a first run can end with nothing discovered, and
        # each used to leave all six examples behind.
        for flags in (
            ("--no-scan", "-y"),
            ("--no-scan",),
            ("-y",),            # scan runs, parent is empty, --yes takes "all of none"
            (),                 # scan runs, parent is empty, nothing to ask about
        ):
            with self.subTest(flags=flags):
                code, out, err = self._init(*flags)
                self.assertEqual(code, 0, err)
                self.assertEqual(self._projects(), [], out)
                self._assert_no_demo_content()
                (self.target / "registry.jsonc").unlink()

    def test_a_scan_that_fails_still_leaves_an_empty_registry(self):
        # scan_projects swallows OSError from iterdir and reports nothing found.
        # "Nothing found" must mean nothing written, not "fall back to the demo".
        original = init_mod.scan_projects
        # The real function's failure path: an unreadable parent yields nothing.
        self.assertEqual(original(self.tmp / "does-not-exist", self.target), [])

        init_mod.scan_projects = lambda parent, exclude: []
        self.addCleanup(setattr, init_mod, "scan_projects", original)
        code, _out, err = self._init("-y")
        self.assertEqual(code, 0, err)
        self.assertEqual(self._projects(), [])
        self._assert_no_demo_content()

    def test_the_empty_array_says_how_to_add_a_project(self):
        code, _out, err = self._init("--no-scan", "-y")
        self.assertEqual(code, 0, err)
        raw = (self.target / "registry.jsonc").read_text(encoding="utf-8")
        start = raw.index('"projects"')
        end = raw.index("]", start)
        block = raw[start:end]
        self.assertIn("//", block, "the empty projects array carries no guidance")
        self.assertIn("paths", block)
        # Guidance only: a comment is not data, and the parse must still be empty.
        self.assertEqual(self._projects(), [])

    def test_a_discovered_project_is_the_only_thing_written(self):
        # The positive path, so the fix cannot be "always write nothing".
        (self.box / "widgets").mkdir()
        (self.box / "widgets" / "README.md").write_text("# widgets\n", encoding="utf-8")
        code, _out, err = self._init("-y")
        self.assertEqual(code, 0, err)
        self.assertEqual([p["id"] for p in self._projects()], ["widgets"])
        self._assert_no_demo_content()


class RenderRegistry(unittest.TestCase):
    """The same rule at the unit level, including the fallback nobody exercises."""

    def test_the_packaged_template_renders_empty(self):
        text = init_mod.render_registry(registry_text(), "/somewhere/projects", [])
        data = loads_jsonc(text)
        self.assertEqual(data["projects"], [])
        for key in init_mod.DEMO_LISTS:
            self.assertFalse(data.get(key), key)

    def test_the_json_fallback_drops_the_examples_too(self):
        # _set_root fails when the template has no defaults.root, which sends
        # render_registry down the json.dumps path. That path used to leave
        # "projects" exactly as the template wrote it.
        template = loads_jsonc(registry_text())
        template.pop("defaults", None)
        text = init_mod.render_registry(
            json.dumps(template, ensure_ascii=False), "/somewhere/projects", []
        )
        data = loads_jsonc(text)
        self.assertEqual(data["projects"], [])
        self.assertEqual(data["defaults"]["root"], "/somewhere/projects")
        for demo in DEMO_IDS:
            self.assertNotIn(demo, text)
        # Even here the reader is told what to type.
        self.assertIn("//", text[text.index('"projects"'):])

    def test_the_comments_that_are_the_documentation_survive(self):
        # The reason the substitution is textual at all. If this stops holding,
        # the fallback is being taken on the happy path and nobody would notice.
        text = init_mod.render_registry(registry_text(), "/somewhere/projects", [])
        self.assertIn("JSONC:", text)
        self.assertIn("ignore_globs", text)


class PointerFile(TempCase):
    """SHOULD-FIX B -- init may not claim a pointer it did not manage to write."""

    def setUp(self):
        super().setUp()
        self.target = self.tmp / "ws"

    def test_the_promise_is_made_only_when_the_pointer_exists(self):
        code, out, err = capture(cli.main, ["init", str(self.target), "-y", "--no-scan"])
        self.assertEqual(code, 0, err)
        self.assertIn("now points here", out)
        self.assertEqual(pointer_file().read_text(encoding="utf-8").strip(), str(self.target))

    def test_a_pointer_that_cannot_be_written_is_reported_not_claimed(self):
        # A file where the config directory should be: mkdir raises, which is the
        # same OSError a read-only or full config home produces.
        blocker = self.xdg / "nextbrief"
        blocker.write_text("not a directory\n", encoding="utf-8")

        code, out, err = capture(cli.main, ["init", str(self.target), "-y", "--no-scan"])
        # Still a usable workspace, so still exit 0 -- but it must not lie.
        self.assertEqual(code, 0, err)
        self.assertNotIn("now points here", out)
        self.assertIn("--workspace", out)
        self.assertIn("NEXTBRIEF_WORKSPACE", out)
        self.assertIn("Could not write", out)

    def test_the_writer_reports_what_happened(self):
        notes = []
        self.assertTrue(init_mod._write_pointer(self.target, notes))
        self.assertEqual(notes, [])

        blocker = self.xdg / "blocked"
        blocker.write_text("not a directory\n", encoding="utf-8")
        original = init_mod.pointer_file
        init_mod.pointer_file = lambda: blocker / "workspace"
        self.addCleanup(setattr, init_mod, "pointer_file", original)
        notes = []
        self.assertFalse(init_mod._write_pointer(self.target, notes))
        self.assertEqual(len(notes), 1)


class SchemaInTheWorkspace(TempCase):
    """SHOULD-FIX C -- the stage-2 prompt names a file init has to have written."""

    def setUp(self):
        super().setUp()
        self.target = self.tmp / "ws"
        code, _out, err = capture(cli.main, ["init", str(self.target), "-y", "--no-scan"])
        self.assertEqual(code, 0, err)

    def test_the_schema_the_prompt_points_at_is_there(self):
        path = self.target / "schema" / "brief.schema.json"
        self.assertTrue(path.is_file(), "init wrote no schema/brief.schema.json")
        # Readable as JSON, or the model has been handed a broken reference.
        self.assertIn("properties", json.loads(path.read_text(encoding="utf-8")))

    def test_every_schema_the_prompt_names_exists(self):
        # Catches the next dangling reference too, not just this one. Limited to
        # schema/: the prompt also names state/*.json, which the pipeline writes
        # on the first run, and CLAUDE.md, which is the user's to add or not.
        for prompt in sorted((self.target / "prompts").glob("*.md")):
            text = prompt.read_text(encoding="utf-8")
            for rel in sorted(set(re.findall(r"\{workspace_root\}/(schema/[\w./-]+)", text))):
                with self.subTest(prompt=prompt.name, path=rel):
                    self.assertTrue(
                        (self.target / rel).exists(),
                        "%s tells the model to read %s, which init never created"
                        % (prompt.name, rel),
                    )

    def test_rerunning_does_not_overwrite_an_edited_schema(self):
        path = self.target / "schema" / "brief.schema.json"
        path.write_text("{ \"edited\": true }\n", encoding="utf-8")
        code, _out, err = capture(cli.main, ["init", str(self.target), "-y", "--no-scan"])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"edited": True})


if __name__ == "__main__":
    unittest.main()

class PointerIsNotStolen(TempCase):
    """The pointer is one line of global state that decides which workspace every
    later bare command reads. init used to overwrite it unconditionally, so
    creating a second workspace -- to try something, to help someone, inside a
    test -- silently redirected the daily brief, and the result still looked like
    a brief.

    That is not hypothetical. A test run of init in a scratch directory took the
    pointer during development, and the next `nextbrief ls` reported an empty
    backlog for a workspace whose backlog was not empty at all.
    """

    def _init(self, name, **extra):
        target = self.tmp / name
        target.mkdir(exist_ok=True)
        return capture(init_mod.init_workspace, str(target), yes=True, scan=False, **extra)

    def _pointer(self):
        return pointer_file().read_text(encoding="utf-8").strip()

    def test_the_first_workspace_claims_it(self):
        self.assertEqual(self._init("a")[0], 0)
        self.assertEqual(self._pointer(), str(self.tmp / "a"))

    def test_a_second_workspace_does_not_take_it(self):
        self._init("a")
        code, out, _ = self._init("b")
        self.assertEqual(code, 0)
        self.assertEqual(self._pointer(), str(self.tmp / "a"))
        # And it has to say so: a silent no-op here is its own trap.
        self.assertIn("default workspace", out.lower())

    def test_set_default_repoints_deliberately(self):
        self._init("a")
        code, _, _ = capture(
            init_mod.init_workspace, str(self.tmp / "b"), yes=True, scan=False, set_default=True
        )
        self.assertEqual(code, 0)
        self.assertEqual(self._pointer(), str(self.tmp / "b"))

    def test_re_initing_the_same_workspace_is_not_a_conflict(self):
        self._init("a")
        code, out, _ = self._init("a")
        self.assertEqual(code, 0)
        self.assertEqual(self._pointer(), str(self.tmp / "a"))
        self.assertNotIn("default workspace is still", out.lower())
