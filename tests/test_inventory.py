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
from nextbrief.annotate import ANNOTATIONS_NAME
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

    def test_capability_is_shown_and_not_folded_into_the_description(self):
        """`capability` reached the inventory and the JSON before it reached the
        listing a person actually reads, so for one release it was recorded,
        stored, shipped -- and invisible unless you opened the file yourself.

        The two have to be separately legible: a description may have been lifted
        out of a manifest, while a capability is always somebody's judgement about
        what the thing built here could become. A reader deciding whether to reuse
        something rather than rebuild it is asking the second question.
        """
        self.assertEqual(capture(cli.main, [
            "--workspace", str(self.ws), "describe", "orchard",
            "A tenancy API.", "--capability", "A general multi-tenant store.",
        ])[0], 0)
        self.assertEqual(capture(sense.main,
                                 ["--workspace", str(self.ws), "--as-of", AS_OF])[0], 0)

        code, out, err = self.run_cmd()
        self.assertEqual(code, 0, err)
        self.assertIn("A tenancy API.", out)
        self.assertIn("A general multi-tenant store.", out)
        # On separate lines, each prefixed -- not run together as one sentence.
        lines = [ln.strip() for ln in out.splitlines()]
        desc = [ln for ln in lines if "A tenancy API." in ln]
        cap = [ln for ln in lines if "A general multi-tenant store." in ln]
        self.assertEqual(len(desc), 1)
        self.assertEqual(len(cap), 1)
        self.assertNotEqual(desc[0], cap[0])
        self.assertNotEqual(cap[0], "A general multi-tenant store.",
                            "the capability needs a prefix, or it reads as a second description")

    def test_a_project_with_no_capability_prints_no_placeholder_for_it(self):
        # Unlike a description, absence here is the normal case: there is no
        # fallback and most projects will never have one. Announcing it on every
        # line would bury the projects that do.
        self.assertEqual(capture(sense.main,
                                 ["--workspace", str(self.ws), "--as-of", AS_OF])[0], 0)
        code, out, err = self.run_cmd()
        self.assertEqual(code, 0, err)
        for locale_text in ("could also serve", "还能用来"):
            self.assertNotIn(locale_text, out)


class TheDescribeCommand(TempCase):
    """Descriptions had no path in.

    `review` captures answers to fixed questions, but a description is free text
    and cannot be multiple choice -- so the only way to supply one was to
    hand-edit `registry.jsonc`, which is exactly the friction the overlay exists
    to remove.
    """

    def setUp(self):
        super().setUp()
        self.ws = self.workspace()
        self.assertEqual(capture(sense.main,
                                 ["--workspace", str(self.ws), "--as-of", AS_OF])[0], 0)

    def run_cmd(self, *argv):
        return capture(cli.main, ["--workspace", str(self.ws), "describe"] + list(argv))

    def _inventory(self):
        return {e["id"]: e for e in json.loads(
            (self.ws / "state" / INVENTORY_NAME).read_text(encoding="utf-8"))["projects"]}

    def test_a_description_reaches_the_inventory_after_a_re_sense(self):
        code, _, err = self.run_cmd("orchard", "The thing that does the thing.")
        self.assertEqual(code, 0, err)
        self.assertEqual(capture(sense.main,
                                 ["--workspace", str(self.ws), "--as-of", AS_OF])[0], 0)
        got = self._inventory()["orchard"]["description"]
        self.assertEqual(got["what"], "The thing that does the thing.")
        self.assertEqual(got["kind"], "declared")
        self.assertEqual(got["source"], "registry")

    def test_the_registry_is_never_written(self):
        before = (self.ws / "registry.jsonc").read_bytes()
        self.assertEqual(self.run_cmd("orchard", "Something.")[0], 0)
        self.assertEqual((self.ws / "registry.jsonc").read_bytes(), before)

    def test_an_unknown_id_is_refused_rather_than_recorded(self):
        # Recording a description nothing will ever read is worse than refusing.
        code, _, err = self.run_cmd("ghost", "Something.")
        self.assertNotEqual(code, 0)
        self.assertIn("ghost", err)
        self.assertFalse((self.ws / ANNOTATIONS_NAME).exists())

    def test_no_arguments_explains_itself(self):
        code, _, err = self.run_cmd()
        self.assertEqual(code, 2)
        self.assertIn("describe", err)

    def test_clearing_has_to_be_explicit(self):
        # `describe <id>` with nothing after it is ambiguous -- a forgotten
        # argument looks identical to an intent to erase -- so it is a usage
        # error, and an empty string is how you actually clear it.
        self.assertEqual(self.run_cmd("orchard", "Something.")[0], 0)
        self.assertEqual(self.run_cmd("orchard")[0], 2)
        self.assertEqual(self.run_cmd("orchard", "")[0], 0)
        self.assertEqual(capture(sense.main,
                                 ["--workspace", str(self.ws), "--as-of", AS_OF])[0], 0)
        got = self._inventory()["orchard"]["description"]
        self.assertNotEqual(got["what"], "Something.")

    def test_a_reworded_question_does_not_destroy_a_description(self):
        """A description was never an answer to a worded question.

        The first version of the version check dropped the whole overlay on a
        wording bump, which would have deleted a sentence someone wrote by hand
        for a reason entirely unrelated to it.
        """
        from nextbrief.annotate import load_annotations
        from nextbrief.paths import resolve_workspace

        self.assertEqual(self.run_cmd("orchard", "Survives a rewording.")[0], 0)
        # Rewrite the file as if it had been recorded under an older wording.
        text = (self.ws / ANNOTATIONS_NAME).read_text(encoding="utf-8")
        body = text[text.index("{"):]
        data = json.loads(body)
        data["asked_version"] = 1
        data["projects"]["orchard"]["ice"] = {"impact": 2}
        (self.ws / ANNOTATIONS_NAME).write_text(json.dumps(data), encoding="utf-8")

        kept = load_annotations(resolve_workspace(str(self.ws)))
        self.assertEqual(kept["orchard"]["description"], "Survives a rewording.")
        self.assertNotIn("ice", kept["orchard"])

    def test_capability_is_recorded_separately_from_the_description(self):
        """A description says what a thing is; capability says what the thing
        built could also serve. Conflating them loses the reuse question, which
        is the one an agent weighing "build or reuse?" actually has."""
        self.assertEqual(self.run_cmd("orchard", "A tenancy API.")[0], 0)
        code, out, err = self.run_cmd(
            "orchard", "--capability", "The isolation layer generalises to any multi-tenant store.")
        self.assertEqual(code, 0, err)
        self.assertEqual(capture(sense.main,
                                 ["--workspace", str(self.ws), "--as-of", AS_OF])[0], 0)
        e = self._inventory()["orchard"]
        self.assertEqual(e["description"]["what"], "A tenancy API.")
        self.assertIn("multi-tenant store", e["capability"]["what"])

    def test_setting_capability_does_not_blank_the_description(self):
        # The flag must not erase what it did not mention.
        self.assertEqual(self.run_cmd("orchard", "A tenancy API.")[0], 0)
        self.assertEqual(self.run_cmd("orchard", "--capability", "Reusable.")[0], 0)
        self.assertEqual(capture(sense.main,
                                 ["--workspace", str(self.ws), "--as-of", AS_OF])[0], 0)
        self.assertEqual(self._inventory()["orchard"]["description"]["what"], "A tenancy API.")

    def test_capability_is_never_derived(self):
        """No file on disk says "this generalises". It is always a declaration,
        and an agent must be able to see that it is reading somebody's optimism
        rather than a fact about the tree."""
        from nextbrief.inventory import capability

        self.assertEqual(capability(None)["kind"], "absent")
        self.assertEqual(capability("Could do more.")["kind"], "declared")
        self.assertEqual(capability("Could do more.")["source"], "registry")


if __name__ == "__main__":
    unittest.main()
