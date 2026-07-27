"""The model shim and the notification sinks.

Both layers exist to be *allowed to be missing*. Stage 2 is the only place this
program spends money, and the whole design says the deterministic brief is what
you rely on -- so the property under test throughout is that nothing here raises,
whatever is or is not installed on the machine.

No test in this file starts a model, opens a socket, or posts a notification.
"""

from __future__ import annotations

import json
import unittest

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
