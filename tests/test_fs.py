"""The write and delete gates.

Two guarantees are under test here, and they fail in opposite directions.

Containment is about *where*: the engine reads a portfolio and writes a
workspace, so a mutation aimed anywhere else is a bug in the caller and must
raise rather than be quietly skipped. The tests below aim at a sibling directory
holding its own ``registry.jsonc``, because a neighbour that also looks like a
workspace is the case a naive prefix check gets wrong.

The human-only rule is about *what*: an entry an agent may not mark ``done`` is
one it may not unlink either. Gate 3 already refuses the terminal status; without
this, the same agent could reach the same end state by deleting the file, and
leave no record that anything was ever there.
"""

from __future__ import annotations

import ast
import unittest

from helpers import REPO_ROOT, TempCase, write_backlog_item

from nextbrief import fs
from nextbrief.fs import ProtectedPathError
from nextbrief.paths import Workspace, WorkspaceError, resolve_workspace


class GateCase(TempCase):
    def setUp(self):
        super().setUp()
        self.ws_root = self.workspace()
        self.ws = resolve_workspace(str(self.ws_root))

        # A neighbour that is itself a plausible workspace. Nothing here may be
        # written, renamed or removed through a gate bound to ours.
        self.outside = self.tmp / "neighbour"
        self.outside.mkdir(parents=True, exist_ok=True)
        (self.outside / "registry.jsonc").write_text("{}\n", encoding="utf-8")
        (self.outside / "notes.md").write_text("untouched\n", encoding="utf-8")


class Containment(GateCase):
    def test_write_refuses_a_neighbour(self):
        with self.assertRaises(WorkspaceError):
            fs.write_text(self.ws, self.outside / "escaped.md", "nope")
        self.assertFalse((self.outside / "escaped.md").exists())

    def test_append_refuses_a_neighbour(self):
        with self.assertRaises(WorkspaceError):
            fs.append_text(self.ws, self.outside / "escaped.log", "nope\n")
        self.assertFalse((self.outside / "escaped.log").exists())

    def test_rewrite_fields_refuses_a_neighbour(self):
        victim = self.outside / "item.md"
        victim.write_text("---\nid: x\npriority: 1\n---\n\nbody\n", encoding="utf-8")
        with self.assertRaises(WorkspaceError):
            fs.rewrite_fields(self.ws, victim, {"priority": 9})
        self.assertIn("priority: 1", victim.read_text(encoding="utf-8"))

    def test_delete_refuses_a_neighbour(self):
        with self.assertRaises(WorkspaceError):
            fs.remove(self.ws, self.outside / "notes.md")
        self.assertTrue((self.outside / "notes.md").is_file())

    def test_rename_refuses_a_neighbour_at_either_end(self):
        inside = self.ws_root / "state" / "scratch.txt"
        fs.write_text(self.ws, inside, "x\n")
        with self.assertRaises(WorkspaceError):
            fs.replace(self.ws, inside, self.outside / "stolen.txt")
        with self.assertRaises(WorkspaceError):
            fs.replace(self.ws, self.outside / "notes.md", inside)
        self.assertTrue(inside.is_file())
        self.assertTrue((self.outside / "notes.md").is_file())

    def test_ensure_dir_refuses_a_neighbour(self):
        with self.assertRaises(WorkspaceError):
            fs.ensure_dir(self.ws, self.outside / "new")
        self.assertFalse((self.outside / "new").exists())

    def test_traversal_out_and_back_is_still_outside(self):
        # `resolve()` before comparing, or `ws/../neighbour/x` reads as inside.
        with self.assertRaises(WorkspaceError):
            fs.write_text(self.ws, self.ws_root / ".." / "neighbour" / "x.md", "nope")
        self.assertFalse((self.outside / "x.md").exists())

    def test_a_split_out_directory_counts_as_inside(self):
        # Inputs version-controlled, artifacts written elsewhere: a supported
        # layout, and both halves are the engine's to write.
        out = self.tmp / "artifacts"
        out.mkdir()
        ws = Workspace(root=self.ws_root, out=out, source="test")
        self.assertTrue(fs.write_text(ws, out / "BRIEF.md", "# brief\n"))
        self.assertTrue((out / "BRIEF.md").is_file())


class WriteSemantics(GateCase):
    def test_identical_content_is_not_rewritten(self):
        target = self.ws_root / "state" / "x.json"
        self.assertTrue(fs.write_text(self.ws, target, "same\n"))
        before = target.stat().st_mtime_ns
        self.assertFalse(fs.write_text(self.ws, target, "same\n"))
        self.assertEqual(target.stat().st_mtime_ns, before)

    def test_skip_identical_can_be_turned_off(self):
        # The sensing stage rewrites unconditionally; its determinism check
        # compares content, not timestamps.
        target = self.ws_root / "state" / "y.json"
        fs.write_text(self.ws, target, "same\n")
        self.assertTrue(fs.write_text(self.ws, target, "same\n", skip_identical=False))

    def test_a_log_append_survives_an_oserror_inside_the_workspace(self):
        blocked = self.ws_root / "log" / "blocked"
        blocked.mkdir(parents=True, exist_ok=True)
        self.assertFalse(fs.append_jsonl(self.ws, blocked, {"a": 1}))


class DeleteGate(GateCase):
    def test_an_ordinary_file_inside_the_workspace_can_be_deleted(self):
        scratch = self.ws_root / "state" / "scratch.json"
        fs.write_text(self.ws, scratch, "{}\n")
        self.assertTrue(fs.remove(self.ws, scratch))
        self.assertFalse(scratch.exists())

    def test_a_missing_file_is_not_an_error_by_default(self):
        self.assertFalse(fs.remove(self.ws, self.ws_root / "state" / "never.json"))

    def test_a_missing_file_can_be_made_an_error(self):
        with self.assertRaises(FileNotFoundError):
            fs.remove(self.ws, self.ws_root / "state" / "never.json", missing_ok=False)

    def test_a_backlog_entry_is_human_only(self):
        path = write_backlog_item(self.ws_root, "bl-1")
        with self.assertRaises(ProtectedPathError):
            fs.remove(self.ws, path)
        self.assertTrue(path.is_file())

    def test_the_registry_is_human_only(self):
        with self.assertRaises(ProtectedPathError):
            fs.remove(self.ws, self.ws.registry_path)
        self.assertTrue(self.ws.registry_path.is_file())

    def test_the_config_is_human_only(self):
        with self.assertRaises(ProtectedPathError):
            fs.remove(self.ws, self.ws.config_path)
        self.assertTrue(self.ws.config_path.is_file())

    def test_a_backlog_entry_cannot_be_renamed_out_of_the_way_either(self):
        # Renaming an entry to state/ would close it just as thoroughly as
        # deleting it, and would look like a move rather than a loss.
        path = write_backlog_item(self.ws_root, "bl-2")
        with self.assertRaises(ProtectedPathError):
            fs.replace(self.ws, path, self.ws_root / "state" / "bl-2.md")
        self.assertTrue(path.is_file())

    def test_directories_are_refused(self):
        # No recursive delete exists, and the refusal is explicit rather than an
        # IsADirectoryError from three frames down.
        state = self.ws_root / "state"
        state.mkdir(parents=True, exist_ok=True)
        with self.assertRaises(ProtectedPathError):
            fs.remove(self.ws, state)
        self.assertTrue(state.is_dir())

    def test_human_only_is_reported_for_the_paths_it_protects(self):
        self.assertTrue(fs.human_only(self.ws, write_backlog_item(self.ws_root, "bl-3")))
        self.assertTrue(fs.human_only(self.ws, self.ws.registry_path))
        self.assertTrue(fs.human_only(self.ws, self.ws.config_path))
        self.assertFalse(fs.human_only(self.ws, self.ws_root / "state" / "snapshot.json"))
        self.assertFalse(fs.human_only(self.ws, self.ws_root / "BRIEF.md"))


class DeclaredEscapes(GateCase):
    def test_an_undeclared_reason_is_refused(self):
        target = self.tmp / "elsewhere" / "config.json"
        with self.assertRaises(WorkspaceError) as caught:
            fs.write_outside_workspace(target, "{}\n", "because-i-said-so")
        self.assertIn("not a declared reason", str(caught.exception))
        self.assertFalse(target.exists())

    def test_the_error_lists_what_is_declared(self):
        with self.assertRaises(WorkspaceError) as caught:
            fs.write_outside_workspace(self.tmp / "x.json", "{}\n", "nope")
        for name in fs.ESCAPES:
            self.assertIn(name, str(caught.exception))

    def test_a_declared_reason_writes(self):
        target = self.tmp / "elsewhere" / "settings.json"
        self.assertTrue(fs.write_outside_workspace(target, "{}\n", "permissions:merge-into"))
        self.assertEqual(target.read_text(encoding="utf-8"), "{}\n")

    def test_every_declared_escape_explains_itself(self):
        # The list is the review surface. An entry with no sentence beside it is
        # an entry nobody can evaluate.
        for name, why in fs.ESCAPES.items():
            self.assertGreater(len(why), 40, "%s has no real explanation" % name)


class TheDoorIsWhereItSaysItIs(unittest.TestCase):
    """Architectural assertions: the gate is only worth what its coverage is."""

    SRC = REPO_ROOT / "src" / "nextbrief"

    def _sources(self):
        return {p.stem: p.read_text(encoding="utf-8") for p in sorted(self.SRC.rglob("*.py"))}

    def test_only_three_modules_mention_the_escape_hatch(self):
        users = {name for name, text in self._sources().items()
                 if "write_outside_workspace" in text}
        self.assertEqual(
            users, {"fs", "cli", "init"},
            "a new module reached for the escape hatch; it belongs in fs.ESCAPES "
            "with a reason, or it should be writing into the workspace")

    def test_every_reason_passed_at_a_call_site_is_declared(self):
        # Catches the typo that would otherwise surface at runtime, on the one
        # path where the user is already mid-install. Parsed rather than grepped
        # so that the assertion is about the argument, not about the text near it.
        found = []
        for name, text in self._sources().items():
            if name == "fs":
                continue
            for node in ast.walk(ast.parse(text)):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                called = getattr(func, "id", None) or getattr(func, "attr", None)
                if called != "write_outside_workspace":
                    continue
                reason = node.args[2] if len(node.args) > 2 else None
                self.assertIsInstance(
                    reason, ast.Constant,
                    "%s passes a computed reason; it must be a literal so this "
                    "check and a reviewer can both read it" % name)
                self.assertIn(reason.value, fs.ESCAPES)
                found.append(reason.value)
        self.assertEqual(sorted(found), sorted(fs.ESCAPES),
                         "ESCAPES and the call sites have drifted apart")

    def test_the_sensing_and_rendering_stages_have_no_way_out(self):
        # The nightly path is sense -> render. Neither imports the escape, so no
        # unattended run can write outside a workspace at all.
        sources = self._sources()
        for stage in ("sense", "render", "html"):
            self.assertNotIn("write_outside_workspace", sources[stage])

    def test_the_raw_frontmatter_writer_stays_private_to_the_gate(self):
        # frontmatter.rewrite_fields is unchecked by design. If anything but fs
        # imports it, the write-permission gate can be handed a path nobody
        # checked -- and that path came out of a file an agent just wrote.
        for name, text in self._sources().items():
            if name in ("fs", "frontmatter"):
                continue
            self.assertNotIn(
                "from .frontmatter import parse_frontmatter, rewrite_fields", text,
                "%s imports the unchecked frontmatter writer" % name)


if __name__ == "__main__":
    unittest.main()
