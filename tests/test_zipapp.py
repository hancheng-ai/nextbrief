"""The single-file .pyz, exercised as the artifact people actually download.

Zipapps fail in ways a source checkout never does, and the failures are quiet.
The one this file exists for: ``python -m zipapp --main "nextbrief.cli:main"``
generates

    import nextbrief.cli
    nextbrief.cli.main()

which discards the return value. Every exit code the CLI defines then reached
the shell as 0 -- ``check`` could not report a stale brief, a missing workspace
looked like a clean run, and anything scheduling the .pyz reported success on
exactly the failures it was installed to catch. The build succeeded throughout,
because zipapp only zips files, and the old smoke test only ran success paths.

So these tests build the real script into a throwaway tree and interrogate the
archive it produces. Slow by unit-test standards (one build, a few seconds) and
worth it: nothing cheaper can observe this class of bug.
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from helpers import TempCase

from nextbrief.jsonc import load_jsonc

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build-zipapp.sh"

_BUILT = {}


def build_once():
    """Build the .pyz once per process, into a tree that is not the repository.

    The script derives its own ROOT from BASH_SOURCE, so copying it next to a
    copy of the package is enough to redirect the whole build -- and keeps the
    suite from overwriting a dist/ the developer may be about to ship.
    """
    if "path" in _BUILT:
        return _BUILT["path"], _BUILT["proc"]
    work = Path(tempfile.mkdtemp(prefix="nextbrief-zipapp-"))
    atexit.register(shutil.rmtree, str(work), ignore_errors=True)
    (work / "src").mkdir(parents=True)
    (work / "scripts").mkdir()
    shutil.copytree(REPO_ROOT / "src" / "nextbrief", work / "src" / "nextbrief")
    shutil.copy2(BUILD_SCRIPT, work / "scripts" / BUILD_SCRIPT.name)
    # Scrubbed here as well as inside the script, because this runs from
    # setUpClass -- before TempCase.setUp has redirected anything -- and so
    # inherits the developer's real environment. A machine that exports
    # NEXTBRIEF_OUT had its own BRIEF.md overwritten by this test, which is the
    # one thing a build is never allowed to touch.
    env = {k: v for k, v in os.environ.items() if not k.startswith("NEXTBRIEF_")}
    env["PYTHON"] = sys.executable
    proc = subprocess.run(
        ["bash", str(work / "scripts" / BUILD_SCRIPT.name)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        timeout=600,
    )
    _BUILT["path"] = work / "dist" / "nextbrief.pyz"
    _BUILT["proc"] = proc
    return _BUILT["path"], proc


@unittest.skipUnless(shutil.which("bash"), "the build script needs bash")
class Zipapp(TempCase):
    @classmethod
    def setUpClass(cls):
        cls.pyz, cls.build = build_once()

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(self.pyz), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=300,
        )

    def test_the_build_and_its_own_smoke_test_pass(self):
        # The script asserts the exit codes too. If it stops doing that, the
        # regression can ship again between releases of this file.
        self.assertEqual(
            self.build.returncode,
            0,
            self.build.stdout.decode("utf-8", "replace"),
        )
        self.assertTrue(self.pyz.is_file())
        log = self.build.stdout.decode("utf-8", "replace")
        self.assertIn("exits 2", log, "the build script no longer asserts the usage code")
        self.assertIn("exits 3", log, "the build script no longer asserts the stale code")

    def test_the_entry_point_propagates_the_return_value(self):
        shim = zipfile.ZipFile(str(self.pyz)).read("__main__.py").decode("utf-8")
        self.assertIn("sys.exit(main())", shim, shim)

    def test_a_missing_workspace_exits_2(self):
        proc = self._run("--workspace", str(self.tmp / "nowhere"), "ls")
        self.assertEqual(proc.returncode, 2, proc.stdout.decode("utf-8", "replace"))

    def test_check_on_a_workspace_with_no_snapshot_exits_3(self):
        # Exit 3 is the whole contract `check` exists for: a scheduler branches
        # on it rather than parsing output.
        ws = self.tmp / "box" / "ws"
        ws.parent.mkdir(parents=True)
        proc = self._run("init", str(ws), "--yes", "--no-scan")
        self.assertEqual(proc.returncode, 0, proc.stdout.decode("utf-8", "replace"))

        proc = self._run("--workspace", str(ws), "v0", "--no-notify")
        self.assertEqual(proc.returncode, 0, proc.stdout.decode("utf-8", "replace"))
        snapshot = ws / "state" / "snapshot.json"
        self.assertTrue(snapshot.is_file())
        snapshot.unlink()

        proc = self._run("--workspace", str(ws), "check")
        self.assertEqual(proc.returncode, 3, proc.stdout.decode("utf-8", "replace"))

    def test_success_still_exits_0(self):
        # The other half of "propagates": a shim that always exited 1 would pass
        # every test above.
        proc = self._run("--version")
        self.assertEqual(proc.returncode, 0, proc.stdout.decode("utf-8", "replace"))

    def test_the_archive_carries_no_compiled_bytecode(self):
        names = zipfile.ZipFile(str(self.pyz)).namelist()
        self.assertEqual([n for n in names if n.endswith(".pyc") or "__pycache__" in n], [])

    def test_the_scaffold_it_ships_is_empty_of_worked_examples(self):
        # Through the artifact, not through an import: the packaged template is
        # read via importlib.resources inside an archive, which is the path most
        # likely to fall back to something unexpected.
        ws = self.tmp / "scaffold" / "ws"
        ws.parent.mkdir(parents=True)
        proc = self._run("init", str(ws), "--yes", "--no-scan")
        self.assertEqual(proc.returncode, 0, proc.stdout.decode("utf-8", "replace"))
        self.assertEqual(load_jsonc(ws / "registry.jsonc")["projects"], [])
        self.assertTrue((ws / "schema" / "brief.schema.json").is_file())
        self.assertIn(
            "properties",
            json.loads((ws / "schema" / "brief.schema.json").read_text(encoding="utf-8")),
        )


if __name__ == "__main__":
    unittest.main()
