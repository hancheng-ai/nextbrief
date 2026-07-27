"""Workspace resolution.

The precedence chain is the whole reason a pip-installed engine can serve someone
else's vault, and the refusal at the end of it is what stops a misconfigured
machine from rendering a clean, plausible, entirely content-free brief.
"""

from __future__ import annotations

import os
import unittest

from helpers import TempCase

from nextbrief import paths
from nextbrief.paths import (
    Workspace,
    WorkspaceError,
    config_home,
    expand,
    pointer_file,
    resolve_workspace,
)


class Resolution(TempCase):
    def setUp(self):
        super().setUp()
        # Four plausible workspaces, one per resolution channel, each with the
        # registry that makes it look like one.
        self.dirs = {}
        for name in ("flag", "env", "pointer", "discovered"):
            d = self.tmp / name
            d.mkdir(parents=True)
            (d / "registry.jsonc").write_text("{}\n", encoding="utf-8")
            self.dirs[name] = d
        self.nested = self.dirs["discovered"] / "a" / "b"
        self.nested.mkdir(parents=True)

        pointer_file().parent.mkdir(parents=True, exist_ok=True)
        pointer_file().write_text(str(self.dirs["pointer"]) + "\n", encoding="utf-8")
        os.environ["NEXTBRIEF_WORKSPACE"] = str(self.dirs["env"])

    def _resolved(self, **kwargs):
        kwargs.setdefault("cwd", self.nested)
        return resolve_workspace(**kwargs)

    def test_flag_beats_everything(self):
        ws = self._resolved(explicit=str(self.dirs["flag"]))
        self.assertEqual(ws.root, self.dirs["flag"].resolve())
        self.assertEqual(ws.source, "--workspace")

    def test_env_beats_pointer_and_discovery(self):
        ws = self._resolved()
        self.assertEqual(ws.root, self.dirs["env"].resolve())
        self.assertIn("NEXTBRIEF_WORKSPACE", ws.source)

    def test_pointer_beats_discovery(self):
        del os.environ["NEXTBRIEF_WORKSPACE"]
        ws = self._resolved()
        self.assertEqual(ws.root, self.dirs["pointer"].resolve())
        self.assertEqual(ws.source, str(pointer_file()))

    def test_upward_search_is_the_last_resort(self):
        del os.environ["NEXTBRIEF_WORKSPACE"]
        pointer_file().unlink()
        ws = self._resolved()
        self.assertEqual(ws.root, self.dirs["discovered"].resolve())
        self.assertEqual(ws.source, "discovered from cwd")

    def test_empty_pointer_file_falls_through(self):
        del os.environ["NEXTBRIEF_WORKSPACE"]
        pointer_file().write_text("\n", encoding="utf-8")
        self.assertEqual(self._resolved().root, self.dirs["discovered"].resolve())

    def test_out_defaults_to_root_and_can_be_split(self):
        out = self.tmp / "elsewhere"
        out.mkdir()
        ws = self._resolved(explicit=str(self.dirs["flag"]))
        self.assertEqual(ws.out, ws.root)
        ws = self._resolved(explicit=str(self.dirs["flag"]), out=str(out))
        self.assertEqual(ws.out, out.resolve())
        self.assertEqual(ws.root, self.dirs["flag"].resolve())

    def test_out_from_the_environment(self):
        out = self.tmp / "outenv"
        out.mkdir()
        os.environ["NEXTBRIEF_OUT"] = str(out)
        self.assertEqual(self._resolved().out, out.resolve())


class Refusal(TempCase):
    """A missing workspace raises. It never defaults to somewhere empty."""

    def test_nothing_configured_anywhere_raises(self):
        bare = self.tmp / "bare"
        bare.mkdir()
        with self.assertRaises(WorkspaceError) as caught:
            resolve_workspace(cwd=bare)
        # The message has to be actionable: this is the first thing a new user
        # sees when they run the tool from the wrong directory.
        self.assertIn("nextbrief init", str(caught.exception))

    def test_directory_without_a_registry_raises(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        with self.assertRaises(WorkspaceError) as caught:
            resolve_workspace(explicit=str(empty))
        self.assertIn("registry.jsonc", str(caught.exception))

    def test_nonexistent_explicit_path_raises(self):
        with self.assertRaises(WorkspaceError):
            resolve_workspace(explicit=str(self.tmp / "absent"))

    def test_require_registry_can_be_waived(self):
        # `init` has to be able to name a directory that is not a workspace yet.
        empty = self.tmp / "empty2"
        empty.mkdir()
        ws = resolve_workspace(explicit=str(empty), require_registry=False)
        self.assertEqual(ws.root, empty.resolve())


class Expansion(TempCase):
    def test_expanduser(self):
        self.assertEqual(expand("~/vault"), self.home / "vault")

    def test_expandvars(self):
        os.environ["NEXTBRIEF_TEST_BASE"] = str(self.tmp)
        self.assertEqual(expand("$NEXTBRIEF_TEST_BASE/vault"), self.tmp / "vault")

    def test_both_at_once_on_the_way_in(self):
        os.environ["NEXTBRIEF_TEST_SUB"] = "vault"
        self.assertEqual(expand("~/$NEXTBRIEF_TEST_SUB"), self.home / "vault")

    def test_resolution_expands_a_pointer_written_with_a_tilde(self):
        vault = self.home / "vault"
        vault.mkdir(parents=True)
        (vault / "registry.jsonc").write_text("{}\n", encoding="utf-8")
        pointer_file().parent.mkdir(parents=True, exist_ok=True)
        pointer_file().write_text("~/vault\n", encoding="utf-8")
        self.assertEqual(resolve_workspace(cwd=self.tmp).root, vault.resolve())

    def test_config_home_follows_xdg(self):
        self.assertEqual(config_home(), self.xdg / "nextbrief")


class Containment(TempCase):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / "ws"
        (self.root / "state").mkdir(parents=True)
        self.out = self.tmp / "out"
        self.out.mkdir()
        self.ws = Workspace(root=self.root.resolve(), out=self.out.resolve(), source="test")

    def test_paths_inside_are_accepted(self):
        self.assertTrue(self.ws.contains(self.root / "state" / "snapshot.json"))
        self.assertTrue(self.ws.contains(self.out / "BRIEF.md"))
        self.assertTrue(self.ws.contains(self.root))

    def test_a_sibling_directory_is_rejected(self):
        sibling = self.tmp / "not-the-workspace"
        sibling.mkdir()
        self.assertFalse(self.ws.contains(sibling / "BRIEF.md"))

    def test_traversal_out_of_the_workspace_is_rejected(self):
        # `contains` resolves before comparing, so ../ cannot smuggle a path out.
        self.assertFalse(self.ws.contains(self.root / ".." / "escaped.md"))
        self.assertFalse(self.ws.contains(self.root / "state" / ".." / ".." / "escaped.md"))

    def test_the_parent_of_the_workspace_is_rejected(self):
        self.assertFalse(self.ws.contains(self.tmp))

    def test_ensure_dirs_creates_only_workspace_directories(self):
        self.ws.ensure_dirs()
        for path in (self.ws.state, self.ws.log, self.ws.backlog):
            self.assertTrue(path.is_dir())
            self.assertTrue(self.ws.contains(path))

    def test_derived_paths_hang_off_root_and_out(self):
        self.assertEqual(self.ws.registry_path, self.root.resolve() / "registry.jsonc")
        self.assertEqual(self.ws.backlog, self.root.resolve() / "backlog")
        self.assertEqual(self.ws.snapshot, self.out.resolve() / "state" / "snapshot.json")
        self.assertEqual(self.ws.brief_md, self.out.resolve() / "BRIEF.md")


class ModuleConstants(unittest.TestCase):
    def test_env_names_are_the_documented_ones(self):
        # Named in the README and in `nextbrief --help`; renaming one silently
        # would break every scheduled job that exports it.
        self.assertEqual(paths.ENV_WORKSPACE, "NEXTBRIEF_WORKSPACE")
        self.assertEqual(paths.ENV_OUT, "NEXTBRIEF_OUT")


if __name__ == "__main__":
    unittest.main()
