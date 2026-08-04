"""The model shim and the notification sinks.

Both layers exist to be *allowed to be missing*. Stage 2 is the only place this
program spends money, and the whole design says the deterministic brief is what
you rely on -- so the property under test throughout is that nothing here raises,
whatever is or is not installed on the machine.

No test in this file starts a model, opens a socket, or posts a notification.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import types
import unittest
from unittest import mock

from helpers import TempCase

from nextbrief import providers, sinks
from nextbrief.paths import resolve_workspace
from nextbrief.providers import ProviderResult


class Naming(unittest.TestCase):
    def test_aliases_people_actually_type(self):
        self.assertEqual(providers.canonical("openai"), "openai_compat")
        self.assertEqual(providers.canonical("OpenAI-Compat"), "openai_compat")
        self.assertEqual(providers.canonical("off"), "none")
        self.assertEqual(providers.canonical(""), providers.DEFAULT_PROVIDER)
        self.assertEqual(providers.canonical(None), providers.DEFAULT_PROVIDER)

    def test_provider_name_defaults_to_auto(self):
        self.assertEqual(providers.provider_name({}), "auto")
        self.assertEqual(providers.provider_name({"model": {"provider": "ollama"}}), "ollama")

    def test_the_shipped_config_shape_resolves(self):
        """The mapping form is what templates/config.example.jsonc writes, and
        therefore what every workspace created by `nextbrief init` contains.

        It used to stringify to "{'name': 'claude', ...}", which is not a known
        provider. Stage 2 then failed soft exactly as designed, so `run` rendered
        a v0 brief and exited 0 -- the model stage was skipped on every install
        that followed the documentation, and the scheduler reported success. The
        suite missed it because every test here used the model-section spelling.
        """
        cfg = {"provider": {"name": "claude", "model": "sonnet", "effort": "low"}}
        self.assertEqual(providers.provider_name(cfg), "claude")

        opts = providers.provider_options(cfg, "claude")
        self.assertEqual(opts.get("model"), "sonnet")
        self.assertEqual(opts.get("effort"), "low")

    def test_every_spelling_of_provider_agrees(self):
        for cfg in (
            {"provider": "claude"},
            {"provider": {"name": "claude"}},
            {"provider": {"name": "CLAUDE"}},
            {"model": {"provider": "claude"}},
        ):
            self.assertEqual(providers.provider_name(cfg), "claude", cfg)

    def test_a_malformed_provider_entry_falls_back_rather_than_raising(self):
        # Nothing here should be a crash: an unusable value means "not
        # configured", and the deterministic brief still gets rendered.
        for cfg in ({"provider": None}, {"provider": []}, {"provider": {}},
                    {"provider": {"name": None}}, {"provider": 17}):
            self.assertIsInstance(providers.provider_name(cfg), str)

    def test_options_layer_stage_defaults_then_shared_then_per_provider(self):
        cfg = {
            "model_by_stage": {"daily": {"model": "small", "effort": "low"}},
            "model": {
                "provider": "claude",
                "timeout_seconds": 60,
                "claude": {"model": "large"},
            },
        }
        opts = providers.provider_options(cfg, "claude")
        self.assertEqual(opts["model"], "large")   # per-provider wins
        self.assertEqual(opts["effort"], "low")    # stage default survives
        self.assertEqual(opts["timeout_seconds"], 60)

    def test_agentic_flag_decides_who_moves_the_data(self):
        # Getting this backwards produces a run that reports success and writes
        # nothing, so it is worth pinning.
        self.assertTrue(providers.is_agentic("claude"))
        self.assertFalse(providers.is_agentic("ollama"))
        self.assertFalse(providers.is_agentic("none"))


class NoModelIsASupportedMode(TempCase):
    def setUp(self):
        super().setUp()
        self.ws = resolve_workspace(str(self.workspace(with_git=False)))

    def test_none_returns_cleanly_and_produces_no_brief(self):
        result = providers.run_provider("none", {}, "any prompt", self.ws)
        self.assertIsInstance(result, ProviderResult)
        # ok=True on purpose: not calling a model is a working configuration, and
        # reporting it as a failure would put a red line in the log every night.
        # What makes it a no-op is the empty text -- the caller writes no brief.
        self.assertTrue(result.ok)
        self.assertEqual(result.text, "")
        self.assertEqual(result.error, "")

    def test_an_empty_prompt_is_refused_before_anything_is_spawned(self):
        result = providers.run_provider("none", {}, "   ", self.ws)
        self.assertFalse(result.ok)
        self.assertIn("empty prompt", result.error)

    def test_none_always_probes_usable(self):
        self.assertTrue(providers.available({})["none"])

    def test_auto_falls_back_to_none_on_a_machine_with_no_runner(self):
        real = dict(providers.PROVIDERS)
        try:
            for name in ("claude", "codex", "ollama", "openai_compat"):
                providers.PROVIDERS[name] = _Unavailable
            self.assertIs(providers.resolve("auto", {}), providers.PROVIDERS["none"].run)
        finally:
            providers.PROVIDERS.clear()
            providers.PROVIDERS.update(real)


class Degradation(TempCase):
    def setUp(self):
        super().setUp()
        self.ws = resolve_workspace(str(self.workspace(with_git=False)))

    def test_an_unknown_provider_name_fails_softly(self):
        result = providers.run_provider("not-a-runner", {}, "prompt", self.ws)
        self.assertFalse(result.ok)
        self.assertIn("unknown provider", result.error)

    def test_a_missing_binary_fails_softly(self):
        cfg = {"model": {"provider": "ollama", "ollama": {"bin": "/nonexistent/ollama"}}}
        result = providers.run_provider("ollama", cfg, "prompt", self.ws)
        self.assertFalse(result.ok)
        self.assertIn("ollama", result.error)

    def test_a_provider_that_raises_costs_a_result_not_the_brief(self):
        real = providers.PROVIDERS.get("none")
        providers.PROVIDERS["none"] = _Exploding
        try:
            result = providers.run_provider("none", {}, "prompt", self.ws)
        finally:
            providers.PROVIDERS["none"] = real
        self.assertFalse(result.ok)
        self.assertIn("RuntimeError", result.error)

    def test_a_provider_returning_the_wrong_type_is_caught(self):
        real = providers.PROVIDERS.get("none")
        providers.PROVIDERS["none"] = _WrongType
        try:
            result = providers.run_provider("none", {}, "prompt", self.ws)
        finally:
            providers.PROVIDERS["none"] = real
        self.assertFalse(result.ok)
        self.assertIn("str", result.error)

    def test_a_broken_probe_does_not_take_down_discovery(self):
        real = providers.PROVIDERS.get("none")
        providers.PROVIDERS["none"] = _BrokenProbe
        try:
            self.assertFalse(providers.available({})["none"])
        finally:
            providers.PROVIDERS["none"] = real

    def test_run_cli_turns_a_missing_binary_into_a_return_code(self):
        rc, out, err = providers.run_cli(["/nonexistent/runner", "--version"])
        self.assertEqual(rc, providers.RC_NOT_FOUND)
        self.assertEqual(out, "")
        self.assertTrue(err)


class Helpers(TempCase):
    def test_unfence_strips_one_wrapping_fence_and_nothing_else(self):
        self.assertEqual(providers.unfence('```json\n{"a": 1}\n```'), '{"a": 1}')
        self.assertEqual(providers.unfence('{"a": 1}'), '{"a": 1}')
        # A reply that is malformed stays malformed: repairing it here would turn
        # a loud failure into a plausible-looking brief.
        self.assertEqual(providers.unfence('```json\n{"a": 1'), '```json\n{"a": 1')

    def test_granted_dirs_are_read_from_the_registry(self):
        # The grant is exactly the tree the registry already claims to describe:
        # the model sees that, the workspace, and nothing else.
        ws_dir = self.workspace(with_git=False)
        ws = resolve_workspace(str(ws_dir))
        dirs = providers.granted_dirs(ws)
        self.assertIn(ws.root, dirs)
        self.assertIn((ws_dir / "projects").resolve(), dirs)

    def test_a_relative_root_is_resolved_against_the_workspace(self):
        # The shipped example declares `"root": "./projects"`. Resolving that
        # against the process's directory would grant a path that does not exist.
        ws_dir = self.workspace(with_git=False)
        registry = json.loads(
            (ws_dir / "registry.jsonc").read_text(encoding="utf-8").split("\n", 1)[1]
        )
        self.assertEqual(registry["defaults"]["root"], "./projects")
        ws = resolve_workspace(str(ws_dir))
        self.assertIn((ws_dir / "projects").resolve(), providers.granted_dirs(ws))

    def test_granted_dirs_fails_open_to_the_workspace_alone(self):
        ws_dir = self.workspace(with_git=False)
        (ws_dir / "registry.jsonc").write_text("{ not json", encoding="utf-8")
        ws = resolve_workspace(str(ws_dir))
        self.assertEqual(providers.granted_dirs(ws), [ws.root])


class Sinks(unittest.TestCase):
    def test_disabled_means_disabled(self):
        self.assertFalse(sinks.notify("t", "b", {"notify": {"enabled": False}}))

    def test_an_empty_body_is_not_worth_an_interruption(self):
        self.assertFalse(sinks.notify("t", "", {"notify": {"backend": "none"}}))

    def test_the_none_backend_delivers_nothing(self):
        self.assertFalse(sinks.notify("t", "b", {"notify": {"backend": "none"}}))

    def test_an_unknown_backend_stays_quiet_rather_than_guessing(self):
        self.assertEqual(sinks.resolve_backend({"notify": {"backend": "carrier-pigeon"}}), "none")
        self.assertFalse(sinks.notify("t", "b", {"notify": {"backend": "carrier-pigeon"}}))

    def test_bodies_are_clipped_to_one_bounded_line(self):
        clipped = sinks.clip("a\nb\n" + "x" * 500)
        self.assertLessEqual(len(clipped), sinks.MAX_LEN)
        self.assertNotIn("\n", clipped)

    def test_a_failing_sink_returns_false_instead_of_raising(self):
        real = sinks.SINKS.get("none")
        sinks.SINKS["none"] = _Exploding
        try:
            self.assertFalse(sinks.notify("t", "b", {"notify": {"backend": "none"}}))
        finally:
            sinks.SINKS["none"] = real


# --- stand-in modules ------------------------------------------------------
#
# Plain classes rather than real modules: the registry is a dict by design, so a
# stand-in only has to answer the same four names.


class _Unavailable:
    NAME = "stub"
    AGENTIC = False

    @staticmethod
    def probe(cfg=None):
        return False

    @staticmethod
    def run(cfg, prompt, ws):
        return ProviderResult(False, "", "not available")


class _Exploding:
    NAME = "stub"
    AGENTIC = False

    @staticmethod
    def probe(cfg=None):
        return True

    @staticmethod
    def run(cfg, prompt, ws):
        raise RuntimeError("the runner fell over")

    @staticmethod
    def send(title, body, cfg=None):
        raise RuntimeError("the notifier fell over")


class _WrongType:
    NAME = "stub"
    AGENTIC = False

    @staticmethod
    def probe(cfg=None):
        return True

    @staticmethod
    def run(cfg, prompt, ws):
        return "not a ProviderResult"


class _BrokenProbe:
    NAME = "stub"
    AGENTIC = False

    @staticmethod
    def probe(cfg=None):
        raise RuntimeError("probe exploded")

    @staticmethod
    def run(cfg, prompt, ws):
        return ProviderResult(True, "", "")


if __name__ == "__main__":
    unittest.main()


class CcNotifySink(unittest.TestCase):
    """Delivery through cc-notify, and the fallback when it is not there.

    macOS draws a banner's icon and its grouping from the sending app, so every
    tool shelling out to a bare `terminal-notifier` shares one identity and piles
    into one Notification Center group. cc-notify solved that for itself and grew
    a `--send` mode so callers can post under their own badge.
    """

    def setUp(self):
        from nextbrief.sinks import cc_notify

        self.mod = cc_notify
        self.tmp = tempfile.mkdtemp(prefix="ccn-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.script = os.path.join(self.tmp, "notify.py")
        with open(self.script, "w", encoding="utf-8") as fh:
            fh.write("#!/usr/bin/env python3\n")

    def _cfg(self, **over):
        cfg = {"notify": {"cc_notify_path": self.script}}
        cfg["notify"].update(over)
        return cfg

    def test_it_is_unavailable_when_the_script_is_not_installed(self):
        """The ordinary case on most machines: not here, not a failure.

        The search paths are patched out. Without that this test passes or fails
        depending on whether the developer happens to have cc-notify installed --
        it failed on the machine it was written on for exactly that reason, which
        is the whole argument against letting a test read the real environment.
        """
        with mock.patch.object(self.mod, "CANDIDATES", ()):
            self.assertFalse(self.mod.available({"notify": {"cc_notify_path": "/nope/notify.py"}}))
            self.assertFalse(self.mod.available(None))
            self.assertFalse(self.mod.send("t", "b", None))

    def test_a_declared_path_is_preferred_over_the_search(self):
        self.assertTrue(self.mod.available(self._cfg()))
        self.assertEqual(self.mod._script(self._cfg()), self.script)

    def test_the_exit_code_is_the_contract(self):
        """cc-notify exits 0 only when a banner was actually delivered.

        That is the whole reason this sink can be tried first: an unauthorized
        bundle id fails silently on macOS, so a caller that assumed success would
        be silently muted. Anything but 0 has to read as "fall back".
        """
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)
            return types.SimpleNamespace(returncode=self.code)

        with mock.patch.object(self.mod.subprocess, "run", fake_run):
            self.code = 0
            self.assertTrue(self.mod.send("t", "b", self._cfg()))
            self.code = 1
            self.assertFalse(self.mod.send("t", "b", self._cfg()))
        self.assertIn("--send", calls[0])
        self.assertIn("--badge", calls[0])
        self.assertIn("nextbrief", calls[0])

    def test_values_are_argv_entries_never_interpolated(self):
        # The body is assembled from project files the engine only reads, so it
        # is hostile by construction. There must be no shell for it to be
        # hostile at, and each value must be its own entry.
        seen = []

        def fake_run(argv, **kw):
            seen.append(argv)
            self.assertNotIn("shell", kw)
            return types.SimpleNamespace(returncode=0)

        nasty = 'x"; rm -rf ~; echo "'
        with mock.patch.object(self.mod.subprocess, "run", fake_run):
            self.mod.send(nasty, nasty, self._cfg(), open_url="/tmp/BRIEF.html")
        argv = seen[0]
        self.assertIn(nasty, argv)
        self.assertEqual(argv[argv.index("--title") + 1], nasty)
        self.assertEqual(argv[argv.index("--open") + 1], "/tmp/BRIEF.html")

    def test_a_hanging_notifier_never_costs_the_run(self):
        # A notification is the least important thing in the pipeline; it must
        # not be able to stall an unattended job.
        def hang(argv, **kw):
            raise subprocess.TimeoutExpired(argv, 1)

        with mock.patch.object(self.mod.subprocess, "run", hang):
            self.assertFalse(self.mod.send("t", "b", self._cfg()))

    def test_a_broken_notifier_never_raises(self):
        def boom(argv, **kw):
            raise OSError("no such thing")

        with mock.patch.object(self.mod.subprocess, "run", boom):
            self.assertFalse(self.mod.send("t", "b", self._cfg()))

    def test_auto_falls_back_when_cc_notify_is_absent(self):
        from nextbrief import sinks

        with mock.patch.object(sinks._cc_notify, "available", lambda cfg=None: False):
            self.assertNotEqual(sinks.resolve_backend({}), "cc-notify")
        with mock.patch.object(sinks._cc_notify, "available", lambda cfg=None: True):
            self.assertEqual(sinks.resolve_backend({}), "cc-notify")

    def test_an_explicit_backend_still_wins(self):
        # Someone who asked for silence, or for a specific sink, must get it
        # whatever else is installed.
        from nextbrief import sinks

        with mock.patch.object(sinks._cc_notify, "available", lambda cfg=None: True):
            self.assertEqual(sinks.resolve_backend({"notify": {"backend": "none"}}), "none")
            self.assertEqual(sinks.resolve_backend({"notify": {"backend": "macos"}}), "macos")
