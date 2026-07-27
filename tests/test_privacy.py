"""The privacy rule, which is the strongest promise this package makes.

A path covered by ``privacy.never_read`` may contribute exactly one thing to the
output: an integer. Its file names must not appear anywhere, in any artifact, in
any form -- and its activity must still be visible as a count, or the rule would
be indistinguishable from "exclude it".

Sensing enforces this three independent times (walk pruning, git pathspec
exclusion, and a pre-write scan of the serialised output). All three are exercised
here, including the last one on its own, because it is the defence that has to
survive a future rewrite of the other two.
"""

from __future__ import annotations

import json
import unittest

from helpers import (
    AS_OF,
    PRIVATE_FILES,
    TempCase,
    base_registry,
    capture,
    git_commit_all,
    git_init,
    requires_git,
    set_tree_mtime,
)

from nextbrief import sense

# The stem every private fixture file shares. If this string appears in an
# artifact, the rule has been broken -- nothing else in the fixture looks like it.
PRIVATE_STEM = "ledger-capture"


class NeverRead(TempCase):
    def setUp(self):
        super().setUp()
        self.ws = self.workspace()
        self.assertEqual(
            capture(sense.main, ["--workspace", str(self.ws), "--as-of", AS_OF])[0], 0
        )
        self.snapshot_text = (self.ws / "state" / "snapshot.json").read_text(encoding="utf-8")
        self.digest_text = (self.ws / "state" / "digest.json").read_text(encoding="utf-8")
        self.snapshot = json.loads(self.snapshot_text)

    def _kiln(self):
        return {p["id"]: p for p in self.snapshot["projects"]}["kiln"]

    def test_no_private_filename_appears_anywhere_in_the_snapshot(self):
        # Deliberately a substring search over the raw serialisation rather than a
        # structured walk: a leak into a field nobody thought of is exactly the
        # failure mode worth catching.
        self.assertNotIn(PRIVATE_STEM, self.snapshot_text)
        for name in PRIVATE_FILES:
            self.assertNotIn(name, self.snapshot_text)

    def test_no_private_filename_appears_in_the_digest(self):
        self.assertNotIn(PRIVATE_STEM, self.digest_text)

    def test_the_private_directory_itself_is_not_enumerated(self):
        kiln = self._kiln()
        for path in kiln["fs"]["top_changed_paths"]:
            self.assertNotIn("fixtures/private", path)
        self.assertNotIn("fixtures/private", str(kiln["fs"]["newest_file_path"]))

    def test_activity_is_still_reported_as_a_count(self):
        # The point of the rule: "three files changed in there" stays sayable;
        # "which three" never becomes sayable.
        self.assertEqual(self._kiln()["private_file_count"], len(PRIVATE_FILES))

    def test_private_files_are_excluded_from_the_ordinary_counts(self):
        # They are counted once, in private_file_count, and nowhere else --
        # otherwise a private directory would inflate the visible activity of the
        # project that happens to contain it.
        kiln = self._kiln()
        self.assertEqual(kiln["fs"]["total_files"], 1)  # kiln/README.md only

    def test_the_declared_reason_is_kept_out_of_the_snapshot_too(self):
        # The reason field is written for a human reading the registry six months
        # later. It has no business in generated output.
        self.assertNotIn("never read them", self.snapshot_text.lower())


class WildcardNeverRead(TempCase):
    """A never_read declaration written as a wildcard must actually be enforced.

    The rule used to be applied by reducing each declaration to a literal
    directory prefix, and every wildcard form reduced to nothing: the count came
    out 0, the git ``:(exclude)`` pathspec was dropped, and the walk collected the
    file names -- with exit 0 and no warning, which is the worst way for a stated
    guarantee to fail. These are the forms a reader will write, because they are
    the forms the registry's own ``ignore_globs`` examples use.
    """

    # Added to the kiln fixture so every glob shape has something to match.
    EXTRA_FILES = (
        "secret-dir/one.txt",
        "secret-dir/two.txt",
        "nested/private/deep.txt",
        "keys/a.key",
        "keys/b.txt",
        "top.key",
    )
    # kiln/README.md + the three PRIVATE_FILES + EXTRA_FILES
    KILN_TOTAL_FILES = 1 + len(PRIVATE_FILES) + len(EXTRA_FILES)

    def _workspace(self, never_read, status_docs=True):
        reg = base_registry()
        reg["projects"][1]["privacy"]["never_read"] = list(never_read)
        if not status_docs:
            reg["projects"][1]["status_docs"] = []
        ws = self.workspace(registry=reg)
        kiln = ws / "projects" / "kiln"
        for rel in self.EXTRA_FILES:
            path = kiln / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("secret\n", encoding="utf-8")
        set_tree_mtime(ws / "projects")
        return ws

    def _sense(self, never_read, status_docs=True):
        """Sense a workspace whose kiln project declares exactly ``never_read``."""
        ws = self._workspace(never_read, status_docs=status_docs)
        code, _out, err = capture(sense.main, ["--workspace", str(ws), "--as-of", AS_OF])
        self.assertEqual(code, 0, err)
        text = (ws / "state" / "snapshot.json").read_text(encoding="utf-8")
        snap = json.loads(text)
        return text, {p["id"]: p for p in snap["projects"]}["kiln"]

    def _sense_git(self, never_read):
        """The same, with kiln as a repository so the pathspec is a real one."""
        ws = self._workspace(never_read)
        kiln = ws / "projects" / "kiln"
        git_init(kiln)
        git_commit_all(kiln, "kiln: fixtures")
        registry = json.loads(
            (ws / "registry.jsonc").read_text(encoding="utf-8").split("\n", 1)[1])
        registry["projects"][1]["git"] = "auto"
        (ws / "registry.jsonc").write_text(json.dumps(registry, indent=2), encoding="utf-8")
        code, _out, err = capture(sense.main, ["--workspace", str(ws), "--as-of", AS_OF])
        self.assertEqual(code, 0, err)
        text = (ws / "state" / "snapshot.json").read_text(encoding="utf-8")
        return text, {p["id"]: p for p in json.loads(text)["projects"]}["kiln"]

    def _assert_counted_and_hidden(self, never_read, count, hidden):
        text, kiln = self._sense(never_read)
        self.assertEqual(kiln["private_file_count"], count,
                         "never_read %r counted the wrong number of files" % (never_read,))
        for name in hidden:
            self.assertNotIn(name, text,
                             "%r leaked a file name covered by %r" % (name, never_read))

    def test_a_literal_subdirectory(self):
        self._assert_counted_and_hidden(["secret-dir"], 2, ["one.txt", "two.txt"])

    def test_a_trailing_slash_is_the_same_declaration(self):
        self._assert_counted_and_hidden(["secret-dir/"], 2, ["one.txt", "two.txt"])

    def test_a_directory_name_at_any_depth(self):
        # Three under kiln/fixtures/private, one under kiln/nested/private.
        self._assert_counted_and_hidden(
            ["**/private/**"], len(PRIVATE_FILES) + 1, list(PRIVATE_FILES) + ["deep.txt"]
        )

    def test_everything_directly_inside_a_directory(self):
        self._assert_counted_and_hidden(["keys/*"], 2, ["a.key", "b.txt"])

    def test_an_extension_glob_is_anchored_where_it_is_written(self):
        # `*.key` covers the keys beside it; `**/*.key` covers them at any depth.
        # One rule for every call site is what keeps the four defences agreeing.
        self._assert_counted_and_hidden(["*.key"], 1, ["top.key"])
        self._assert_counted_and_hidden(["**/*.key"], 2, ["top.key", "a.key"])

    @requires_git
    def test_a_pattern_that_matches_nothing_is_not_an_error(self):
        _text, kiln = self._sense_git(["no-such-directory/**"])
        self.assertEqual(kiln["private_file_count"], 0)
        # ...and nothing is excluded from git on the strength of it: an exclusion
        # switches on history simplification, so inventing one changes the
        # commit counts of a project that declared nothing private at all.
        self.assertEqual(
            [s for s in kiln["git"][0]["pathspec"] if s.startswith(":(exclude)")], []
        )
        self.assertTrue(kiln["git"][0]["whole_repo"])

    def test_a_pattern_that_matches_everything(self):
        text, kiln = self._sense(["**"], status_docs=False)
        self.assertEqual(kiln["private_file_count"], self.KILN_TOTAL_FILES)
        # The whole tree is private, so it contributes a number and nothing else.
        self.assertEqual(kiln["fs"]["total_files"], 0)
        self.assertEqual(kiln["fs"]["top_changed_paths"], [])
        for name in ("secret-dir", "top.key", "deep.txt") + PRIVATE_FILES:
            self.assertNotIn(name, text)

    def test_a_status_document_inside_a_never_read_tree_stops_the_run(self):
        # The registry contradicts itself: this document is both "read it and
        # report its date" and "never read it". Refusing loudly is the only
        # answer that cannot be wrong; quietly honouring either half is a
        # decision no tool should make on the reader's behalf.
        ws = self._workspace(["**"], status_docs=True)
        code, _out, err = capture(sense.main, ["--workspace", str(ws), "--as-of", AS_OF])
        self.assertEqual(code, sense.EXIT_PRIVACY)
        self.assertIn("refusing to write", err)
        self.assertFalse((ws / "state" / "snapshot.json").exists())

    @requires_git
    def test_a_wildcard_declaration_reaches_the_git_pathspec(self):
        # Defence 2: the exclusion has to name a real directory, so it is derived
        # from what the glob matched on disk rather than from the glob itself --
        # `:(exclude)` takes a pathspec, and git does not read our globs.
        text, kiln = self._sense_git(["**/private/**"])
        self.assertNotIn(PRIVATE_STEM, text)
        pathspec = kiln["git"][0]["pathspec"]
        self.assertIn(":(exclude)fixtures/private", pathspec)
        self.assertIn(":(exclude)nested/private", pathspec)
        self.assertEqual(kiln["private_file_count"], len(PRIVATE_FILES) + 1)


class GlobSemantics(unittest.TestCase):
    """The matcher the four defences share, pinned on its own.

    fnmatch cannot express any of these: it compiles ``*`` to ``.*``, so ``**``
    never collapses to nothing and a single ``*`` crosses directories.
    """

    def test_double_star_collapses_to_nothing(self):
        gs = sense.GlobSet(["**/private/**"])
        self.assertTrue(gs.matches("private/a.txt"))
        self.assertTrue(gs.matches("deep/nested/private/a.txt"))
        self.assertFalse(gs.matches("private"))
        self.assertFalse(gs.matches("public/a.txt"))

    def test_a_single_star_stays_inside_one_segment(self):
        gs = sense.GlobSet(["keys/*"])
        self.assertTrue(gs.matches("keys/a.key"))
        # ...but a directory it matched is covered whole, contents included.
        self.assertTrue(gs.matches("keys/sub/deeper.key"))
        self.assertFalse(gs.matches("other/a.key"))

    def test_a_directory_is_covered_by_everything_under_it(self):
        self.assertTrue(sense.GlobSet(["vault/**"]).covers_dir("vault"))
        self.assertTrue(sense.GlobSet(["vault"]).covers_dir("vault"))
        self.assertTrue(sense.GlobSet(["vault/*"]).covers_dir("vault"))
        # An extension glob covers no directory: the walk must descend and look.
        self.assertFalse(sense.GlobSet(["*.key"]).covers_dir("keys"))

    def test_character_classes_and_question_marks_survive(self):
        gs = sense.GlobSet(["log-?.[ab]"])
        self.assertTrue(gs.matches("log-1.a"))
        self.assertFalse(gs.matches("log-12.a"))
        self.assertFalse(gs.matches("log-1.c"))


class PrivacyGlobs(unittest.TestCase):
    """The globs are rebased onto the shared root, because a private directory
    nested inside *another* project's tree must be pruned when that project is
    walked as well."""

    REGISTRY = {
        "projects": [
            {
                "id": "kiln",
                "paths": ["kiln"],
                "privacy": {"never_read": ["fixtures/private/**", "secrets"]},
            },
            {"id": "orchard", "paths": ["orchard"]},
        ]
    }

    def test_globs_are_root_relative(self):
        self.assertEqual(
            sense.privacy_globs(self.REGISTRY),
            ["kiln/fixtures/private/**", "kiln/secrets"],
        )

    def test_rebasing_onto_a_containing_project(self):
        globs = sense.privacy_globs(self.REGISTRY)
        # Walking `orchard` need not care about kiln's private paths...
        self.assertEqual(sense.rebase_globs(globs, "orchard"), [])
        # ...but walking `kiln` must, expressed relative to kiln.
        self.assertEqual(
            sense.rebase_globs(globs, "kiln"), ["fixtures/private/**", "secrets"]
        )
        # A path that *is* the private directory prunes its whole subtree.
        self.assertEqual(sense.rebase_globs(globs, "kiln/secrets"), ["**"])

    def test_matching_is_structural_not_substring(self):
        globs = sense.privacy_globs(self.REGISTRY)
        self.assertTrue(sense._matches_private("kiln/fixtures/private/a.json", globs))
        # Prose that merely mentions the directory is not a leak.
        self.assertFalse(
            sense._matches_private("Never open kiln/fixtures/private by hand", globs)
        )
        self.assertFalse(sense._matches_private("kiln/fixtures/generated/a.json", globs))

    def test_absolute_paths_are_matched_after_the_root_is_stripped(self):
        globs = sense.privacy_globs(self.REGISTRY)
        self.assertTrue(
            sense._matches_private("/example/root/kiln/secrets", globs, "/example/root")
        )

    def test_find_private_leaks_reports_where_not_what(self):
        globs = sense.privacy_globs(self.REGISTRY)
        blob = {"projects": [{"fs": {"top": ["kiln/fixtures/private/a.json"]}}]}
        leaks = sense.find_private_leaks(blob, globs, "", "snapshot")
        self.assertEqual(len(leaks), 1)
        where, value = leaks[0]
        self.assertEqual(where, "snapshot.projects[0].fs.top[0]")
        self.assertEqual(value, "kiln/fixtures/private/a.json")


class PreWriteGuard(TempCase):
    """The last line of defence, tested by defeating the first two.

    ``build`` is replaced with one that smuggles a private path into the
    structure, which is precisely the shape a future refactor of the walk would
    take. Nothing may be written or printed after that.
    """

    LEAKED = "kiln/fixtures/private/ledger-capture-0001.json"

    def setUp(self):
        super().setUp()
        self.ws = self.workspace()
        self.real_build = sense.build

        def leaky_build(*args, **kwargs):
            snap = self.real_build(*args, **kwargs)
            snap["projects"][1]["fs"]["top_changed_paths"].append(self.LEAKED)
            return snap

        sense.build = leaky_build
        self.addCleanup(setattr, sense, "build", self.real_build)

    def test_the_run_aborts_with_the_privacy_exit_code(self):
        code, out, err = capture(sense.main, ["--workspace", str(self.ws), "--as-of", AS_OF])
        self.assertEqual(code, sense.EXIT_PRIVACY)
        self.assertIn("refusing to write", err)

    def test_nothing_is_written(self):
        capture(sense.main, ["--workspace", str(self.ws), "--as-of", AS_OF])
        self.assertFalse((self.ws / "state" / "snapshot.json").exists())
        self.assertFalse((self.ws / "state" / "digest.json").exists())

    def test_the_leaked_value_is_never_printed(self):
        # Reporting a leak by quoting it would leak it. Only the location is named.
        code, out, err = capture(sense.main, ["--workspace", str(self.ws), "--as-of", AS_OF])
        self.assertEqual(code, sense.EXIT_PRIVACY)
        self.assertNotIn("ledger-capture", out + err)
        self.assertIn("at snapshot", err)

    def test_stdout_mode_is_guarded_too(self):
        # Printing a private file name leaks it exactly as thoroughly as writing it.
        code, out, err = capture(
            sense.main, ["--workspace", str(self.ws), "--as-of", AS_OF, "--stdout"]
        )
        self.assertEqual(code, sense.EXIT_PRIVACY)
        self.assertNotIn("ledger-capture", out)


class GitPathspecExclusion(TempCase):
    """Defence 2: a private file name must not arrive through git either."""

    @requires_git
    def test_committed_private_files_do_not_reach_the_snapshot(self):
        ws = self.workspace()
        # Make kiln a repository *with the private fixtures committed*, so that
        # git log and git status both know their names.
        kiln = ws / "projects" / "kiln"
        git_init(kiln)
        git_commit_all(kiln, "kiln: fixtures")
        registry = json.loads((ws / "registry.jsonc").read_text(encoding="utf-8").split("\n", 1)[1])
        registry["projects"][1]["git"] = "auto"
        (ws / "registry.jsonc").write_text(json.dumps(registry, indent=2), encoding="utf-8")

        code, _, err = capture(sense.main, ["--workspace", str(ws), "--as-of", AS_OF])
        self.assertEqual(code, 0, err)
        text = (ws / "state" / "snapshot.json").read_text(encoding="utf-8")
        self.assertNotIn(PRIVATE_STEM, text)
        snap = json.loads(text)
        kiln_entry = {p["id"]: p for p in snap["projects"]}["kiln"]
        self.assertTrue(kiln_entry["has_git"])
        self.assertEqual(kiln_entry["private_file_count"], len(PRIVATE_FILES))


if __name__ == "__main__":
    unittest.main()
