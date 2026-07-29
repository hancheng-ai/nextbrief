"""The inventory: what each project *is*, as against what it did this week.

`digest.json` is an activity report. It answers what moved, how fresh it is and
what is due — exactly what the brief needs — and never what a project is *for*.
An agent asked "should we build X?" needs the other question answered, and
re-deriving it by walking the tree is what every agent otherwise does separately,
every session.

The load-bearing property is not the content but the labelling: a reader must be
able to tell a sentence lifted out of a manifest, which they can go and check,
from a sentence its owner typed. Blend those and a declaration reads as a
finding, which is the mistake this codebase has made twice already.
"""

from __future__ import annotations

import json
import unittest

from helpers import AS_OF, TempCase, capture

from nextbrief import cli, sense
from nextbrief.inventory import INVENTORY_NAME, describe


class WhereADescriptionComesFrom(TempCase):
    def setUp(self):
        super().setUp()
        self.proj = self.tmp / "thing"
        self.proj.mkdir(parents=True)

    def test_a_package_manifest_is_read_and_cited(self):
        (self.proj / "package.json").write_text(
            json.dumps({"description": "A tool for the thing."}), encoding="utf-8")
        got = describe(self.proj)
        self.assertEqual(got["what"], "A tool for the thing.")
        self.assertEqual(got["kind"], "observed")
        self.assertEqual(got["source"], "package.json")

    def test_a_readme_is_the_fallback_and_is_also_cited(self):
        (self.proj / "README.md").write_text(
            "# Thing\n\n[![badge](x)](y)\n\nIt does the thing.\n", encoding="utf-8")
        got = describe(self.proj)
        self.assertEqual(got["what"], "It does the thing.")
        self.assertEqual(got["source"], "README.md")

    def test_headings_and_badges_are_not_a_description(self):
        (self.proj / "README.md").write_text("# Title\n===\n![x](y)\n", encoding="utf-8")
        self.assertIsNone(describe(self.proj)["what"])

    def test_a_manifest_beats_a_readme(self):
        (self.proj / "package.json").write_text(
            json.dumps({"description": "From the manifest."}), encoding="utf-8")
        (self.proj / "README.md").write_text("From the readme.\n", encoding="utf-8")
        self.assertEqual(describe(self.proj)["source"], "package.json")

    def test_what_the_owner_declared_wins_and_says_so(self):
        (self.proj / "package.json").write_text(
            json.dumps({"description": "Stale manifest text."}), encoding="utf-8")
        got = describe(self.proj, declared="What it actually is.")
        self.assertEqual(got["what"], "What it actually is.")
        self.assertEqual(got["kind"], "declared")
        self.assertEqual(got["source"], "registry")

    def test_nothing_is_invented_when_there_is_nothing(self):
        # Roughly half a real portfolio -- the content projects -- have no
        # manifest at all. "No description anywhere" is itself worth reporting:
        # it is the one thing a person can fix in ten seconds.
        got = describe(self.proj)
        self.assertIsNone(got["what"])
        self.assertEqual(got["kind"], "absent")
        self.assertIsNone(got["source"])

    def test_a_paragraph_is_reduced_to_its_first_sentence(self):
        # A manifest description is often a full paragraph: useful on a package
        # page, noise in a list of twelve.
        (self.proj / "package.json").write_text(json.dumps(
            {"description": "It does the thing. Then it does another thing. "
                            "And it goes on at some length after that."}), encoding="utf-8")
        self.assertEqual(describe(self.proj)["what"], "It does the thing.")

    def test_a_sentence_that_never_ends_is_capped(self):
        (self.proj / "package.json").write_text(
            json.dumps({"description": "x" * 900}), encoding="utf-8")
        self.assertLessEqual(len(describe(self.proj)["what"]), 200)

    def test_an_unreadable_manifest_degrades_to_the_readme(self):
        (self.proj / "package.json").write_text("{ not json", encoding="utf-8")
        (self.proj / "README.md").write_text("Still describable.\n", encoding="utf-8")
        self.assertEqual(describe(self.proj)["source"], "README.md")


class ThroughAFullRun(TempCase):
    def setUp(self):
        super().setUp()
        self.ws = self.workspace()
        code, _, err = capture(sense.main, ["--workspace", str(self.ws), "--as-of", AS_OF])
        self.assertEqual(code, 0, err)
        self.inv = json.loads(
            (self.ws / "state" / INVENTORY_NAME).read_text(encoding="utf-8"))

    def test_sense_writes_one_entry_per_project(self):
        self.assertEqual([e["id"] for e in self.inv["projects"]], ["kiln", "orchard"])

    def test_it_is_far_smaller_than_the_digest(self):
        # An artifact that costs as much to read as re-deriving it would have
        # saved nobody anything.
        digest = (self.ws / "state" / "digest.json").read_text(encoding="utf-8")
        inv = (self.ws / "state" / INVENTORY_NAME).read_text(encoding="utf-8")
        self.assertLess(len(inv), len(digest))

    def test_every_entry_says_where_its_description_came_from(self):
        for e in self.inv["projects"]:
            self.assertIn(e["description"]["kind"], ("declared", "observed", "absent"))
            if e["description"]["kind"] == "absent":
                self.assertIsNone(e["description"]["what"])
            else:
                self.assertTrue(e["description"]["source"])

    def test_it_carries_the_edges_an_agent_needs(self):
        for e in self.inv["projects"]:
            for key in ("needs", "unlocks", "serves", "run", "stacks"):
                self.assertIsInstance(e[key], list, key)

    def test_it_is_deterministic(self):
        first = (self.ws / "state" / INVENTORY_NAME).read_text(encoding="utf-8")
        self.assertEqual(capture(sense.main,
                                 ["--workspace", str(self.ws), "--as-of", AS_OF])[0], 0)
        self.assertEqual((self.ws / "state" / INVENTORY_NAME).read_text(encoding="utf-8"),
                         first)


class TheContextCommand(TempCase):
    def setUp(self):
        super().setUp()
        self.ws = self.workspace()

    def run_cmd(self, *extra):
        return capture(cli.main, ["--workspace", str(self.ws), "context"] + list(extra))

    def test_it_says_what_to_run_when_there_is_no_inventory(self):
        code, _, err = self.run_cmd()
        self.assertNotEqual(code, 0)
        self.assertIn("sense", err)

    def test_it_prints_the_portfolio(self):
        self.assertEqual(capture(sense.main,
                                 ["--workspace", str(self.ws), "--as-of", AS_OF])[0], 0)
        code, out, err = self.run_cmd()
        self.assertEqual(code, 0, err)
        self.assertIn("orchard", out)
        self.assertIn("kiln", out)

    def test_json_prints_the_file_verbatim_for_another_tool(self):
        self.assertEqual(capture(sense.main,
                                 ["--workspace", str(self.ws), "--as-of", AS_OF])[0], 0)
        code, out, err = self.run_cmd("--json")
        self.assertEqual(code, 0, err)
        parsed = json.loads(out)
        self.assertEqual(
            parsed,
            json.loads((self.ws / "state" / INVENTORY_NAME).read_text(encoding="utf-8")))

    def test_it_writes_nothing(self):
        self.assertEqual(capture(sense.main,
                                 ["--workspace", str(self.ws), "--as-of", AS_OF])[0], 0)
        from helpers import tree_state

        before = tree_state(self.ws)
        self.assertEqual(self.run_cmd()[0], 0)
        self.assertEqual(tree_state(self.ws), before)


if __name__ == "__main__":
    unittest.main()
