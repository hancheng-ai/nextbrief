"""Adoption of directories the registry never named.

The bug this exists to prevent has no symptom. A project started after the
workspace was set up is simply absent: no error, no empty section, no dropped
claim -- the brief reports confidently on everything else and reads exactly like
a brief for a week in which nothing else happened. So most of what is asserted
below is about what discovery *declines* to adopt, because the failure mode of
getting that wrong is the opposite and just as quiet: a build directory ranked
against real work.
"""

from __future__ import annotations

import json
import unittest

from helpers import (
    AS_OF,
    TempCase,
    capture,
    make_project_entry,
    make_snapshot,
    make_workspace,
)

from nextbrief import discovery, sense
from nextbrief.discovery import claimed_segments, discover
from nextbrief.paths import Workspace


class DiscoverCase(TempCase):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / "portfolio"
        self.root.mkdir(parents=True, exist_ok=True)
        self.ws_root = self.tmp / "workspace"
        self.ws_root.mkdir(parents=True, exist_ok=True)
        self.ws = Workspace(root=self.ws_root, out=self.ws_root, source="test")

    def dirs(self, *names):
        for name in names:
            (self.root / name).mkdir(parents=True, exist_ok=True)

    def found(self, reg):
        return [e["id"] for e in discover(self.root, reg, self.ws)]


class Adoption(DiscoverCase):
    def test_an_unclaimed_directory_is_adopted(self):
        self.dirs("newthing")
        self.assertEqual(self.found({}), ["newthing"])

    def test_the_entry_is_shaped_like_a_hand_written_one(self):
        self.dirs("newthing")
        entry = discover(self.root, {}, self.ws)[0]
        self.assertEqual(entry["paths"], ["newthing"])
        self.assertEqual(entry["name"], "newthing")
        self.assertTrue(entry["discovered"])

    def test_no_tier_and_no_ice_are_stated_rather_than_guessed_at(self):
        # A synthesised midpoint is not a neutral guess, it is an assertion, and
        # `tier in ("flagship", "active")` is the entry condition for the
        # neglected and stalled verdicts. A placeholder tier would have the engine
        # invent an importance and then report the consequences back as a finding.
        self.dirs("newthing")
        entry = discover(self.root, {}, self.ws)[0]
        self.assertIsNone(entry["tier"])
        self.assertIsNone(entry["ice"])
        self.assertIsNone(entry["goal_one_line"])
        self.assertIsNone(discovery.DISCOVERED_TIER)
        self.assertIsNone(discovery.DISCOVERED_ICE)

    def test_version_control_is_checked_rather_than_assumed(self):
        # `auto` on a directory that is not a repository is reported by sense as
        # "declared as a git project but no repository root could be resolved" --
        # untrue of an entry nobody declared, and it spends the run's
        # parse-failure count on a non-problem.
        self.dirs("plain", "versioned")
        (self.root / "versioned" / ".git").mkdir()
        by_id = {e["id"]: e for e in discover(self.root, {}, self.ws)}
        self.assertEqual(by_id["plain"]["git"], "none")
        self.assertEqual(by_id["versioned"]["git"], "auto")

    def test_a_directory_holding_repositories_still_counts_as_versioned(self):
        # A folder of several checkouts is a shape people keep, and it is not the
        # same thing as a folder with no version control anywhere in it.
        self.dirs("container")
        (self.root / "container" / "repo-a").mkdir()
        (self.root / "container" / "repo-a" / ".git").mkdir()
        self.assertEqual(discover(self.root, {}, self.ws)[0]["git"], "auto")

    def test_no_goal_is_invented(self):
        # The one field that would be a fabrication rather than a placeholder.
        self.dirs("newthing")
        self.assertIsNone(discover(self.root, {}, self.ws)[0]["goal_one_line"])

    def test_a_readme_becomes_a_status_doc_at_medium_authority(self):
        self.dirs("newthing")
        (self.root / "newthing" / "README.md").write_text("# hi\n", encoding="utf-8")
        docs = discover(self.root, {}, self.ws)[0]["status_docs"]
        self.assertEqual(docs, [{"path": "newthing/README.md", "kind": "status",
                                 "authority": "medium"}])

    def test_only_one_status_doc_is_taken(self):
        # Listing four is how a brief ends up citing a changelog as a status
        # declaration.
        self.dirs("newthing")
        for name in ("README.md", "ROADMAP.md", "CHANGELOG.md"):
            (self.root / "newthing" / name).write_text("x\n", encoding="utf-8")
        self.assertEqual(len(discover(self.root, {}, self.ws)[0]["status_docs"]), 1)

    def test_a_name_that_is_not_a_slug_still_yields_a_usable_id(self):
        self.dirs("Orchard Site v2")
        self.assertEqual(self.found({}), ["orchard-site-v2"])

    def test_order_is_deterministic(self):
        self.dirs("zeta", "alpha", "mid")
        self.assertEqual(self.found({}), sorted(self.found({})))


class WhatIsAlreadyClaimed(DiscoverCase):
    def test_a_declared_project_is_not_adopted_twice(self):
        self.dirs("declared", "undeclared")
        reg = {"projects": [{"id": "declared", "paths": ["declared"]}]}
        self.assertEqual(self.found(reg), ["undeclared"])

    def test_a_nested_declaration_claims_its_top_directory(self):
        # `atlas/apps/site` must claim `novel`, or discovery adopts the parent of
        # a tree that is already sensed and every file in it is counted twice.
        self.dirs("atlas", "other")
        reg = {"projects": [{"id": "site", "paths": ["atlas/apps/site"]}]}
        self.assertEqual(self.found(reg), ["other"])

    def test_watch_infra_and_archived_all_claim(self):
        self.dirs("watched", "infra-thing", "old", "free")
        reg = {
            "watch": [{"path": "watched"}],
            "infra": [{"path": "infra-thing"}],
            "archived": [{"path": "old", "last_active": "2024-11"}],
        }
        self.assertEqual(self.found(reg), ["free"])

    def test_ignored_finally_does_something(self):
        # It was read only by init before this, so a daily run ignored `ignored`.
        self.dirs("junk", "real")
        reg = {"ignored": [{"path": "junk", "reason": "not mine"}]}
        self.assertEqual(self.found(reg), ["real"])

    def test_a_bare_string_entry_is_honoured_too(self):
        self.dirs("junk", "real")
        self.assertEqual(self.found({"ignored": ["junk"]}), ["real"])

    def test_claimed_segments_collects_every_list(self):
        reg = {
            "projects": [{"id": "a", "paths": ["one/deep/deeper", "two"]}],
            "watch": [{"path": "three"}],
            "ignored": [{"path": "four/five"}],
        }
        self.assertEqual(claimed_segments(reg), {"one", "two", "three", "four"})


class WhatIsNeverAProject(DiscoverCase):
    def test_dotfile_directories_are_skipped(self):
        self.dirs(".claude", ".config", "real")
        self.assertEqual(self.found({}), ["real"])

    def test_build_output_and_home_folders_are_skipped(self):
        self.dirs("node_modules", "dist", "Downloads", "real")
        self.assertEqual(self.found({}), ["real"])

    def test_the_workspace_itself_is_never_adopted(self):
        # Otherwise the brief reports on the thing writing it.
        inside = self.root / "workspace"
        inside.mkdir()
        ws = Workspace(root=inside, out=inside, source="test")
        self.dirs("real")
        self.assertEqual([e["id"] for e in discover(self.root, {}, ws)], ["real"])

    def test_a_split_out_directory_is_not_adopted_either(self):
        out = self.root / "artifacts"
        out.mkdir()
        self.dirs("real")
        ws = Workspace(root=self.ws_root, out=out, source="test")
        self.assertEqual([e["id"] for e in discover(self.root, {}, ws)], ["real"])

    def test_the_engines_own_checkout_is_skipped(self):
        # A developer's checkout sits beside their projects more often than not,
        # and it is the one directory guaranteed to look busy while being the
        # tool rather than the work.
        self.dirs("nextbrief", "real")
        (self.root / "nextbrief" / "pyproject.toml").write_text(
            '[project]\nname = "nextbrief"\nversion = "0.0.0"\n', encoding="utf-8")
        self.assertEqual(self.found({}), ["real"])

    def test_another_packages_checkout_is_not_skipped(self):
        # The rule is "this engine", not "anything with a pyproject".
        self.dirs("somelib")
        (self.root / "somelib" / "pyproject.toml").write_text(
            '[project]\nname = "somelib"\nversion = "1.0"\n', encoding="utf-8")
        self.assertEqual(self.found({}), ["somelib"])

    def test_files_in_the_root_are_not_directories_and_are_ignored(self):
        (self.root / "loose.md").write_text("x\n", encoding="utf-8")
        self.dirs("real")
        self.assertEqual(self.found({}), ["real"])

    def test_an_id_collision_does_not_shadow_a_declared_project(self):
        self.dirs("Thing")
        reg = {"projects": [{"id": "thing", "paths": ["elsewhere"]}]}
        found = discover(self.root, reg, self.ws)
        self.assertTrue(all(e["id"] != "thing" for e in found))


class EndToEnd(TempCase):
    """Discovery through a real sense run, which is the only thing that proves
    the merge happens before privacy globs and the project walk read the list."""

    def setUp(self):
        super().setUp()
        self.ws = make_workspace(self.tmp / "ws")

    def sense(self, *args):
        return capture(sense.main,
                       ["--workspace", str(self.ws), "--as-of", AS_OF] + list(args))

    def snapshot(self):
        return json.loads((self.ws / "state" / "snapshot.json").read_text(encoding="utf-8"))

    def test_a_directory_added_after_setup_reaches_the_snapshot(self):
        # The fixture registry declares orchard and kiln only.
        newcomer = self.ws / "projects" / "latecomer"
        newcomer.mkdir(parents=True)
        (newcomer / "README.md").write_text("# Latecomer\n\nStarted today.\n", encoding="utf-8")

        code, _, err = self.sense()
        self.assertEqual(code, 0, err)
        ids = [p["id"] for p in self.snapshot()["projects"]]
        self.assertIn("latecomer", ids)
        self.assertIn("orchard", ids)

    def test_the_snapshot_says_which_entries_nobody_declared(self):
        newcomer = self.ws / "projects" / "latecomer"
        newcomer.mkdir(parents=True)
        self.assertEqual(self.sense()[0], 0)
        by_id = {p["id"]: p for p in self.snapshot()["projects"]}
        self.assertFalse(by_id["latecomer"]["declared"])
        self.assertTrue(by_id["orchard"]["declared"])

    def test_discovery_does_not_disturb_a_fully_declared_portfolio(self):
        # Nothing undeclared in the fixture, so the snapshot must be what it was.
        self.assertEqual(self.sense()[0], 0)
        first = (self.ws / "state" / "snapshot.json").read_text(encoding="utf-8")
        self.assertEqual(self.sense()[0], 0)
        self.assertEqual((self.ws / "state" / "snapshot.json").read_text(encoding="utf-8"), first)
        self.assertEqual([p["id"] for p in self.snapshot()["projects"]], ["orchard", "kiln"])

    def test_a_forgotten_directory_is_never_called_neglected(self):
        """The harm a placeholder tier caused, stated as a test.

        Make a directory, poke it once, forget it. Thirty-one days later the brief
        used to announce that it had been neglected -- about a project nobody had
        ever said mattered. The engine asserted the importance itself, via the
        synthesised tier, then reported the consequences of its own assertion back
        to its owner as a finding. With discovery adopting the whole root, every
        abandoned experiment would do this, permanently.
        """
        from nextbrief import render

        old = {"best_kind": "file_mtime", "best_date": "2025-11-01", "days_since": 200,
               "signal": "dormant", "caveat_code": None, "caveat": None}
        discovered = make_project_entry(pid="latecomer", tier=None, ice=None,
                                        declared=False, evidence=old)
        stated = make_project_entry(pid="chosen", tier="active", evidence=old)

        meta = render.classify(make_snapshot(projects=[discovered, stated]), [], {}, {})
        neglected = {p["id"] for p in meta["neglected"]}
        self.assertNotIn("latecomer", neglected, "nagged about a project nobody chose")
        self.assertIn("chosen", neglected, "a stated priority should still be flagged")

    def test_a_discovered_project_is_still_deterministic(self):
        (self.ws / "projects" / "latecomer").mkdir(parents=True)
        self.assertEqual(self.sense()[0], 0)
        first = (self.ws / "state" / "snapshot.json").read_text(encoding="utf-8")
        self.assertEqual(self.sense()[0], 0)
        self.assertEqual((self.ws / "state" / "snapshot.json").read_text(encoding="utf-8"), first)


if __name__ == "__main__":
    unittest.main()
