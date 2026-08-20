"""nextbrief -- what happened across your projects, and what you do next.

Three stages, and the order matters:

    sense    deterministic   filesystem + git  ->  snapshot.json
    interpret    a model     snapshot          ->  brief.json
    render   deterministic   brief + snapshot  ->  BRIEF.md / BRIEF.html

The third stage is the point. Every claim the model makes carries an ``evidence``
reference; the renderer resolves each one against the snapshot and drops what it
cannot verify into ``log/rejected.jsonl``. "Do not invent progress" stops being a
line in a prompt that a model may drift from, and becomes a property of the
pipeline that drift cannot defeat.
"""

__version__ = "0.4.0rc3"
__all__ = ["__version__", "build_version"]


# Computed at most once per process. Both callers ask on a path where the answer
# cannot change while the process lives, and one of them (`--version`) is on the
# argument parser, which is built on every single invocation.
_BUILD_VERSION = None


def build_version():
    """The release version, and which checkout it is when it is running from one.

        nextbrief 0.2.1                     a released wheel, or the zipapp
        nextbrief 0.2.1+dev.g1a2b3c4        an editable install from a checkout
        nextbrief 0.2.1+dev.g1a2b3c4.dirty  ... with uncommitted changes

    The question this answers is **"am I the tree or the package"**, not "am I
    identical to a release". Trying to answer the second one -- clean, and
    exactly at the release tag, therefore indistinguishable from the wheel --
    would report the plain version for a checkout that is one commit ahead, and
    one commit ahead is the entire failure this exists to catch: an install that
    silently stopped being the code its owner was reading.

    The release constant above stays plain, and nothing here writes to it --
    which is why the suffix is computed at runtime and used only for display
    and for the provenance stamp on a generated artifact, so `bump-version.sh`,
    the three files that carry a version, and the release workflow's byte-for-
    byte tag comparison all keep working untouched.

    **PyPI rejects local version identifiers, and that is the point.** A build
    carrying `+dev.g...` physically cannot be uploaded. This is a latch, not an
    inconvenience -- do not engineer around it.

    Every failure returns the plain version and says nothing: no git, no
    checkout, an unborn HEAD, a subprocess error, a timeout. `--version` must
    never raise and must never hang, and neither must `sense`, which stamps this
    into every snapshot it writes.

    The line below is the only place in the package that reads the release
    constant, and that is deliberate: it is rewritten by `bump-version.sh` with
    a regex, three files have to agree afterwards, and a constant read from
    several places is a constant several places can be wrong about. Grepping
    the package for its name should therefore find the definition, the export,
    and one use -- `_local_version` answers a narrower question and never sees
    it, so a reader doing impact analysis is not handed prose to sift.
    """
    global _BUILD_VERSION
    if _BUILD_VERSION is None:
        _BUILD_VERSION = __version__ + _local_version()
    return _BUILD_VERSION


def _local_version(here=None):
    """The PEP 440 local segment for this build -- ``+dev.g1a2b3c4[.dirty]``.

    Empty when there is nothing true to say, which is every failure and also the
    ordinary case of a released wheel. Returning the segment rather than the
    whole version string is what keeps the release constant out of this function
    entirely: a probe that cannot establish anything returns nothing, instead of
    returning the thing it was asked to decorate.

    `here` is a parameter so the cases this has to get right can be built and
    run: a tree with no `.git`, a path through an archive, a checkout with no
    commits. Defaulting it rather than reading a global is what lets each of
    those be a test instead of a claim in a comment.
    """
    # Imported inside the function, not at module scope: this module is imported
    # on every invocation, including the ones that never ask what build they are.
    import os
    import subprocess

    if here is None:
        here = os.path.dirname(os.path.abspath(__file__))

    # The zipapp, settled before any path walking. Inside an archive `__file__`
    # is a path *through* a file, so the package directory is not a directory --
    # which is exactly the distinction being drawn, and it holds wherever the
    # .pyz happens to sit. Checking only for a nearby `.git` would not: a zipapp
    # built into the root of its own checkout has one two levels up.
    if not os.path.isdir(here):
        return ""

    # Two levels, and deliberately no further. A src-layout checkout keeps
    # `.git` at <root>/.git with the package at <root>/src/nextbrief, so two is
    # all that can ever be needed -- while walking further would eventually find
    # the `.git` of whatever else the machine keeps under version control. A
    # venv several directories deep inside a home directory that is itself a
    # repository would then stamp this build with a commit from someone's
    # dotfiles.
    root = None
    candidate = here
    for _ in range(2):
        candidate = os.path.dirname(candidate)
        if os.path.exists(os.path.join(candidate, ".git")):
            root = candidate
            break
    if root is None:
        return ""

    def git(*args):
        try:
            proc = subprocess.run(
                ["git", "-C", root] + list(args),
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5,
            )
        except Exception:      # no git binary, a timeout, anything at all
            return None
        if proc.returncode != 0:
            return None
        return proc.stdout.decode("utf-8", "replace").strip()

    sha = git("rev-parse", "--short=7", "HEAD")
    # `isalnum` is the guard that keeps a malformed answer from producing an
    # invalid PEP 440 local version, which would be a worse outcome than saying
    # nothing: a version string no index and no parser will accept.
    if not sha or not sha.isalnum():
        return ""

    dirty = git("status", "--porcelain")
    # Not "assume clean". A dirty tree reported as clean is the one reading that
    # would send somebody looking for a bug in the commit named here, in a tree
    # that does not match it.
    if dirty is None:
        return ""

    return "+dev.g%s%s" % (sha, ".dirty" if dirty else "")
