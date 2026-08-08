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
import re
import unittest

from helpers import AS_OF, REPO_ROOT, TempCase, base_registry, capture

from nextbrief import cli, sense
from nextbrief.annotate import ANNOTATIONS_NAME
from nextbrief.inventory import (
    CAPABILITY_KINDS,
    DESCRIPTION_KINDS,
    INVENTORY_NAME,
    INVENTORY_SCHEMA_VERSION,
    describe,
)


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


SCHEMA_DOC = REPO_ROOT / "docs" / "INVENTORY_SCHEMA.md"

# The published field set of every `schema_version`, written out as a literal.
#
# Derived from the code, it would agree with whatever the code currently does,
# which is the one thing this table must never do. It is a transcript of a
# promise made to readers outside this repository, so it changes when the promise
# does and not when the implementation does.
#
# Keys are paths into the document: "" is the envelope, "projects[]" is any one
# entry, "projects[].description" is that sub-object on any entry.
FIELDS_BY_VERSION = {
    1: {
        "": frozenset({"schema_version", "generated_at", "root", "projects"}),
        "projects[]": frozenset({
            "id", "name", "path", "description", "capability", "goal",
            "stacks", "run", "declared", "status", "positioning",
            "serves", "needs", "unlocks", "has_git",
        }),
        "projects[].description": frozenset({"what", "kind", "source"}),
        "projects[].capability": frozenset({"what", "kind", "source"}),
    },
}

BUMP = ("The published field set of inventory.json changed under "
        "schema_version %d.\n"
        "This is a contract read outside this repository, so:\n"
        "  1. bump INVENTORY_SCHEMA_VERSION in src/nextbrief/inventory.py\n"
        "  2. add the new field set to FIELDS_BY_VERSION in this file\n"
        "  3. update docs/INVENTORY_SCHEMA.md and CHANGELOG.md\n"
        "at %s: ")


def document_shape(doc):
    """Every field path in a document, mapped to the key sets seen there.

    A set of key sets per path rather than one: two project entries that
    disagree about their fields is exactly the drift worth catching, and a union
    would hide it.
    """
    shape = {}

    def note(path, obj):
        shape.setdefault(path, set()).add(frozenset(obj))

    note("", doc)
    for entry in doc.get("projects") or []:
        note("projects[]", entry)
        for sub in ("description", "capability"):
            if isinstance(entry.get(sub), dict):
                note("projects[].%s" % sub, entry[sub])
    return shape


class TheContractIsVersioned(TempCase):
    """`inventory.json` is the one artifact an agent reads *before* it works, and
    it was the only one shipping without a version marker -- `snapshot.json` has
    carried `schema_version` since its second shape.

    That was survivable while the only reader was in this repository, where a
    rename costs a test edit. It stops being survivable the moment a plugin, a
    skill or somebody else's tool reads it: the same rename becomes a silent
    breakage in a program nobody here maintains, discovered on a day its owner
    was doing something else. Silent wrongness is the thing this engine exists to
    refuse, so the contract gets a version before the consumers arrive rather
    than after the first one breaks.

    Run through `sense.main` rather than calling `inventory_document` directly.
    The unit that builds the dict is not the thing consumers read -- the file on
    disk is -- and a hand-built dict has confirmed the wrong reasoning here
    before.
    """

    def setUp(self):
        """A portfolio built to walk every branch that emits a contract object.

        The default two-project fixture does not. Both its projects get their
        description from a README, so the manifest branch of `describe()`, the
        declared branch, and the absent branch are all unreached -- and a field
        added on an unreached branch never appears in the document the guard
        below inspects. That is not a worry, it is a measurement: adding a key to
        the manifest branch and running this class left every test green.

        So: four projects, one per branch, and
        `test_the_fixture_reaches_every_shape_the_contract_can_take` fails if
        that ever stops being true.
        """
        super().setUp()
        reg = base_registry()
        reg["projects"].append(
            {"id": "silo", "name": "Silo", "paths": ["silo"], "git": "none"})
        reg["projects"].append(
            {"id": "void", "name": "Void", "paths": ["void"], "git": "none"})
        self.ws = self.workspace(registry=reg)

        # silo: a package manifest, so `describe()` takes the manifest branch.
        (self.ws / "projects" / "silo").mkdir(parents=True, exist_ok=True)
        (self.ws / "projects" / "silo" / "package.json").write_text(
            json.dumps({"description": "Reads a manifest and stops there."}),
            encoding="utf-8")
        # void: nothing at all, so `describe()` reaches its `absent` return.
        (self.ws / "projects" / "void").mkdir(parents=True, exist_ok=True)

        code, _, err = capture(sense.main, ["--workspace", str(self.ws), "--as-of", AS_OF])
        self.assertEqual(code, 0, err)
        # kiln: a declared description. orchard: a declared capability, which
        # has no other way to exist -- nothing on disk states one.
        for argv in (["describe", "kiln", "A sentence somebody typed."],
                     ["describe", "orchard", "--capability", "Generalises past its purpose."]):
            self.assertEqual(
                capture(cli.main, ["--workspace", str(self.ws)] + argv)[0], 0, argv)
        code, _, err = capture(sense.main, ["--workspace", str(self.ws), "--as-of", AS_OF])
        self.assertEqual(code, 0, err)

        self.raw = (self.ws / "state" / INVENTORY_NAME).read_text(encoding="utf-8")
        self.inv = json.loads(self.raw)

    def test_the_fixture_reaches_every_shape_the_contract_can_take(self):
        """The guard is only as good as the branches the fixture walks.

        Kept as its own test rather than folded into `setUp` so that a fixture
        that stops reaching a branch fails by name, instead of quietly weakening
        the assertion next door.
        """
        for sub, domain in (("description", DESCRIPTION_KINDS),
                            ("capability", CAPABILITY_KINDS)):
            seen = {e[sub]["kind"] for e in self.inv["projects"]}
            # Both directions named: a value the fixture stops reaching and a
            # value the code invented read very differently to whoever is
            # holding the failure, and the same assertion catches both.
            self.assertEqual(
                sorted(seen), sorted(domain),
                "%s.kind -- declared but never reached by this fixture: %s; "
                "produced but not in the module's domain: %s"
                % (sub, sorted(set(domain) - seen) or "none",
                   sorted(seen - set(domain)) or "none"))
        # `observed` covers two different branches that return two different
        # dicts, and only the citation tells them apart.
        sources = {e["description"]["source"] for e in self.inv["projects"]}
        for want, branch in (("package.json", "the manifest branch"),
                             ("README.md", "the README branch"),
                             ("registry", "the declared branch")):
            self.assertIn(want, sources, "%s of describe() is unwalked" % branch)

    def test_the_document_says_which_contract_it_is(self):
        self.assertEqual(self.inv.get("schema_version"), INVENTORY_SCHEMA_VERSION)
        self.assertIsInstance(self.inv["schema_version"], int)

    def test_the_field_set_cannot_change_without_the_version_changing(self):
        """The guard this whole item is for.

        Add a field, drop one, or rename one, and this goes red -- and stays red
        until the version is bumped and the new set recorded above. Which is the
        point: the failure is not "you changed the shape", it is "you changed the
        shape without telling anyone".
        """
        version = self.inv.get("schema_version")
        expected = FIELDS_BY_VERSION.get(version)
        self.assertIsNotNone(
            expected,
            "schema_version %r has no recorded field set. If you just bumped it, "
            "record the new shape in FIELDS_BY_VERSION in this file and in "
            "docs/INVENTORY_SCHEMA.md." % (version,))

        # Before comparing anything: the fixture has to have reached the code.
        # A per-entry check over an empty list passes without looking at
        # anything, and looks identical to a passing test.
        self.assertTrue(self.inv.get("projects"),
                        "the fixture produced no project entries, so the "
                        "per-entry half of this assertion would pass vacuously")

        got = document_shape(self.inv)
        self.assertEqual(
            sorted(got), sorted(expected),
            "the set of objects in inventory.json changed: %s"
            % sorted(set(got) ^ set(expected)))

        for path in sorted(expected):
            seen = got[path]
            self.assertEqual(
                len(seen), 1,
                "entries disagree about their fields at %r: %s"
                % (path, [sorted(s) for s in seen]))
            fields = next(iter(seen))
            self.assertEqual(
                sorted(fields), sorted(expected[path]),
                (BUMP % (version, path or "the top level"))
                + "%s" % sorted(fields ^ expected[path]))

    def test_the_command_an_outside_tool_calls_carries_it_too(self):
        # `context --json` is the documented entry point for another program;
        # the file is an implementation detail of where it lives. It prints the
        # bytes verbatim today, but "today" is why this is asserted separately.
        code, out, err = capture(
            cli.main, ["--workspace", str(self.ws), "context", "--json"])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out).get("schema_version"),
                         INVENTORY_SCHEMA_VERSION)

    def test_the_sentinel_values_are_a_closed_set(self):
        """`kind` is what a consumer branches on, so its domain is part of the
        contract in the way a field name is.

        Read out of the module source rather than out of a run: the fixture has
        no project with a declared capability, so a test that only inspected
        output would never see `declared` and would happily pass while the code
        emitted a fourth value nothing documents.
        """
        src = (REPO_ROOT / "src" / "nextbrief" / "inventory.py").read_text(
            encoding="utf-8")
        emitted = set(re.findall(r'"kind":\s*"([a-z_]+)"', src))
        self.assertTrue(emitted, "the kind literals stopped parsing")
        self.assertEqual(
            sorted(emitted), sorted(set(DESCRIPTION_KINDS) | set(CAPABILITY_KINDS)),
            "inventory.py emits a `kind` that DESCRIPTION_KINDS/CAPABILITY_KINDS "
            "do not declare, or declares one it never emits")

    def test_absent_never_arrives_with_a_value_beside_it(self):
        # The three fields move together, and docs/INVENTORY_SCHEMA.md tells
        # consumers they may check `kind` alone. That permission has to be true.
        checked = 0
        for entry in self.inv["projects"]:
            for sub in ("description", "capability"):
                obj = entry[sub]
                checked += 1
                if obj["kind"] == "absent":
                    self.assertIsNone(obj["what"], sub)
                    self.assertIsNone(obj["source"], sub)
                else:
                    self.assertIsNotNone(obj["source"], sub)
        self.assertGreater(checked, 0, "no sub-object was examined")


class TheContractDocument(unittest.TestCase):
    """The doc is the deliverable, not the table above -- a promise nobody
    outside can read is not a promise. So it is checked against the same literal
    the guard uses, in both directions.

    One-directional would be worse than nothing: a doc that merely fails to
    mention a new field still reads as complete.
    """

    def _text(self):
        return SCHEMA_DOC.read_text(encoding="utf-8")

    def _field_rows(self):
        """Rows of the tables headed `| Field | Promise | …`, as (name, promise).

        Only those tables: the value-domain tables are headed `| Value |` and
        their first cell is a `kind` value, not a field name.
        """
        rows, inside = [], False
        for line in self._text().splitlines():
            if line.startswith("| Field | Promise |"):
                inside = True
                continue
            if not inside:
                continue
            if not line.startswith("|"):
                inside = False
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if set(cells[0]) <= set("-: "):
                continue
            name = re.match(r"^`([a-z_]+)`$", cells[0])
            self.assertIsNotNone(name, "unparseable field row: %r" % line)
            rows.append((name.group(1), cells[1]))
        return rows

    def _domain_values(self, heading):
        text = self._text()
        start = text.index(heading) + len(heading)
        rest = text[start:]
        end = min((i for i in (rest.find("\n### "), rest.find("\n---")) if i >= 0),
                  default=len(rest))
        return set(re.findall(r"^\| `([a-z_]+)` \|", rest[:end], re.MULTILINE))

    def test_it_documents_exactly_the_fields_that_ship(self):
        # Not FIELDS_BY_VERSION[...] directly: a fresh bump would raise KeyError
        # here, and a traceback is a worse instruction than a sentence.
        table = FIELDS_BY_VERSION.get(INVENTORY_SCHEMA_VERSION)
        self.assertIsNotNone(
            table, "schema_version %d has no field set in FIELDS_BY_VERSION; "
                   "record the new shape there first."
                   % INVENTORY_SCHEMA_VERSION)
        promised = {name for keys in table.values() for name in keys}
        documented = {name for name, _ in self._field_rows()}
        self.assertTrue(documented, "the field tables stopped parsing")
        self.assertEqual(sorted(documented), sorted(promised),
                         "docs/INVENTORY_SCHEMA.md and the shipped field set "
                         "disagree: %s" % sorted(documented ^ promised))

    def test_every_field_is_marked_stable_or_may_change(self):
        # The whole reason a consumer reads this file. A field with no promise
        # against it is one they cannot make a decision about.
        rows = self._field_rows()
        self.assertTrue(rows)
        for name, promise in rows:
            self.assertIn(promise, ("stable", "may change"),
                          "%r carries no usable promise (%r)" % (name, promise))

    def test_the_kind_domains_are_pinned_and_match_the_code(self):
        self.assertEqual(self._domain_values("### `description.kind`"),
                         set(DESCRIPTION_KINDS))
        self.assertEqual(self._domain_values("### `capability.kind`"),
                         set(CAPABILITY_KINDS))

    def test_it_states_the_current_version(self):
        # The worked example at the top is the first thing anyone reads, and an
        # example showing a version that is no longer shipped teaches the wrong
        # number to every consumer who skims.
        #
        # `assertTrue` rather than `assertIn`: the latter appends the entire
        # document to its message, and 12KB of markdown in a CI log is how a
        # maintainer learns to skim past the line they needed to read.
        want = '"schema_version": %d' % INVENTORY_SCHEMA_VERSION
        self.assertTrue(want in self._text(),
                        "the worked example in docs/INVENTORY_SCHEMA.md does not "
                        "show %s" % want)


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
