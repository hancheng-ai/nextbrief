#!/usr/bin/env python3
"""Refuse to publish shapes that are never publishable, whoever they belong to.

    scripts/leak-shapes.py --range <old>..<new>   .githooks/pre-push
    scripts/leak-shapes.py --all                  a whole history, by hand
    scripts/leak-shapes.py --worktree             before committing

An absolute home directory path, a private key header, a connection string with
the password still in it, an API token. Each is a *shape*: recognisable without
knowing whose it is. That is the whole scope of this file, and the reason it can
live in a public repository and run in a public CI log -- it names nobody, so its
output discloses nothing.

**It scans commits, not the working tree.** A clean tip says nothing about what a
push contains. History is what gets published, so history is what gets scanned --
every commit in the range, not just the one in front of you. `--worktree` exists
for the check you run before committing, which is a different question.

**Know what it does not cover.** It catches a *shape*. It cannot catch an example
copied out of real notes and relabelled, where the name has been changed and
every specific that made it worth copying -- a file and line number, a status, a
date -- is still somebody's real detail. There is no shape left to match on. That
case is caught by reading, by the rule in CONTRIBUTING.md, and by whatever
additional checks a maintainer runs on their own machine against material this
repository has no copy of.

So: green here is not "this push is clean". It is "this push contains none of the
half-dozen things that are obviously never publishable". Treat it as the floor.

**CI running it is a report, not a fence.** By the time a CI job goes red the
objects are on a public server, and a force-push does not retract them --
unreferenced commits stay retrievable by SHA and the events feed lists those
SHAs. A pull request is public from the moment it opens. The job earns its place
for one case the hook cannot cover: hooks are not cloned, so a fresh clone that
never ran `git config core.hooksPath .githooks` has no check at all. Minutes
rather than never.

Exit codes: 0 clean, 1 findings, 2 the scan could not run. The last is a failure
on purpose: a check that cannot run must never be mistaken for one that passed.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

EXIT_OK, EXIT_FOUND, EXIT_ERROR = 0, 1, 2

# POSIX ERE, because that is what `git grep -E` speaks. No \b, no \s, no (?i),
# no (?:...) -- those are PCRE, they need `git grep -P`, and -P is absent from
# git builds compiled without libpcre. Case folding is a per-pattern flag rather
# than an inline (?i).
#
# None of these name anybody, which is what makes this file publishable. Note
# that the character classes stop each pattern from matching its own source: the
# text `/Users/[A-Za-z0-9._-]+/` is not itself an absolute path, because `[` is
# not in the class. A pattern written as a literal would flag this file forever.
SHAPES = [
    (r"/Users/[A-Za-z0-9._-]+/", "an absolute macOS home path", False),
    (r"/home/[A-Za-z0-9._-]+/", "an absolute Linux home path", False),
    (r"C:\\\\Users\\\\[A-Za-z0-9._-]+", "an absolute Windows home path", False),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "a private key", False),
    (r"(postgres|postgresql|mysql|mongodb|redis|amqp)://[^[:space:]:@/]+:[^[:space:]@/]+@",
     "a connection string carrying a password", True),
    (r"sk-[A-Za-z0-9]{20,}", "an API key", True),
    (r"ghp_[A-Za-z0-9]{20,}", "a GitHub token", True),
    (r"xox[baprs]-[A-Za-z0-9-]{10,}", "a Slack token", True),
    (r"AKIA[A-Z0-9]{16}", "an AWS access key id", False),
]


def run(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)


def commits_for(scope: str, rng: str | None) -> list[str]:
    if scope == "worktree":
        return []
    if scope == "all":
        out = run(["git", "rev-list", "--all"])
    else:
        old, _, new = rng.partition("..")
        if set(old) <= {"0"}:
            # A new branch: git offers no "before". Everything already on a
            # remote-tracking ref has been published once, so what this push
            # actually adds is the rest. Falling back to "every commit" instead
            # would re-report the whole history on every feature branch, and a
            # guard that fires thirty-five times per branch is one people learn
            # to pass with --no-verify.
            out = run(["git", "rev-list", new, "--not", "--remotes"])
            if out.returncode != 0 or not out.stdout.strip():
                out = run(["git", "rev-list", new])
        else:
            out = run(["git", "rev-list", "%s..%s" % (old, new)])
    if out.returncode != 0:
        sys.stderr.write("leak-shapes: git rev-list failed: %s\n" % out.stderr.strip())
        sys.exit(EXIT_ERROR)
    return [c for c in out.stdout.split() if c]


def grep(pattern: str, commits: list[str], ignore_case: bool) -> list[str]:
    """git grep over a commit list, or the worktree when the list is empty."""
    args = ["git", "grep", "-nI", "-E"]
    if ignore_case:
        args.append("-i")
    if not commits:
        # --worktree. Without this, git grep searches tracked files only, and a
        # leak in the file you are about to `git add` is invisible to the check
        # whose entire purpose is to run before you commit it. Ignored files stay
        # out, which is right: they are not going anywhere.
        args.append("--untracked")
    args += ["-e", pattern]
    args += commits if commits else ["--", "."]
    out = run(args)
    if out.returncode not in (0, 1):
        sys.stderr.write("leak-shapes: git grep failed (%s): %s\n"
                         % (out.returncode, out.stderr.strip()))
        sys.exit(EXIT_ERROR)
    return [ln for ln in out.stdout.splitlines() if ln.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Scan for shapes that are never publishable.")
    scope = ap.add_mutually_exclusive_group(required=True)
    scope.add_argument("--range", dest="rng", metavar="OLD..NEW",
                       help="the commits a push would add")
    scope.add_argument("--all", action="store_true", help="every reachable commit")
    scope.add_argument("--worktree", action="store_true",
                       help="the working tree, before committing")
    args = ap.parse_args()

    if run(["git", "rev-parse", "--git-dir"]).returncode != 0:
        sys.stderr.write("leak-shapes: not a git repository\n")
        return EXIT_ERROR

    which = "all" if args.all else "worktree" if args.worktree else "range"
    commits = commits_for(which, args.rng)

    if which == "range" and not commits:
        print("leak-shapes: nothing new to scan")
        return EXIT_OK

    where = "%d commit(s)" % len(commits) if commits else "the working tree"
    print("leak-shapes: %s" % where)

    status = EXIT_OK
    # The matched line is printed. None of these name a person, and you cannot
    # fix what you cannot see.
    for pattern, label, fold in SHAPES:
        for hit in grep(pattern, commits, ignore_case=fold):
            print("  FOUND %s\n    %s" % (label, hit))
            status = EXIT_FOUND

    if status == EXIT_OK:
        print("leak-shapes: no matches. This is the generic pass, and it is the"
              " floor rather than the fence -- see the module docstring.")
    else:
        print("\nThese must not be published. Fix them in the commits themselves:"
              "\na follow-up commit does not help, because a push publishes the"
              "\nhistory and not just the tip. See CONTRIBUTING.md.")
    return status


if __name__ == "__main__":
    sys.exit(main())
