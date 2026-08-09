# Homebrew formula for nextbrief.
#
# The source of truth lives here, in the project repository, and is copied into
# the tap when a release is cut. Keeping it here means the formula is reviewed
# in the same pull request as the change that would break it -- a tap that only
# finds out at `brew install` time is a tap nobody trusts.
#
# `<owner>` below is the GitHub account that owns
# https://github.com/hancheng-ai/nextbrief. Homebrew shortens a tap repository
# named `homebrew-tap` to `<owner>/tap` on the command line, so the two spellings
# below refer to the same repository.
#
# Create the tap, once:
#
#   gh repo create <owner>/homebrew-tap --public -d "Homebrew tap for nextbrief"
#   git clone https://github.com/<owner>/homebrew-tap
#   mkdir -p homebrew-tap/Formula
#   cp packaging/homebrew/nextbrief.rb homebrew-tap/Formula/nextbrief.rb
#
# On every later release, bump `version` and re-derive the checksum from the
# published asset -- the url is built from `version`, so one edit moves both:
#
#   V=0.2.1rc1
#   curl -fsSLO "https://github.com/hancheng-ai/nextbrief/releases/download/v$V/nextbrief-$V.tar.gz"
#   shasum -a 256 "nextbrief-$V.tar.gz"      # paste over the sha256 below
#   cd homebrew-tap
#   git add Formula/nextbrief.rb && git commit -m "nextbrief $V" && git push
#
# The same digest is in the release's own SHA256SUMS asset, and GitHub reports it
# without a download: `gh release view v$V --json assets`.
#
# Install and verify from the tap:
#
#   brew tap <owner>/tap
#   brew install nextbrief
#   brew test nextbrief
#   brew audit --strict --online <owner>/tap/nextbrief
#
# The tap does not exist yet. Until it does, this file installs on its own --
# either from the pinned release below, or from `main` via the head stanza:
#
#   brew install --build-from-source ./packaging/homebrew/nextbrief.rb
#   brew install --HEAD --build-from-source ./packaging/homebrew/nextbrief.rb

class Nextbrief < Formula
  desc "Daily brief across your projects, gated on evidence that has to resolve"
  homepage "https://github.com/hancheng-ai/nextbrief"

  # The sdist uploaded by .github/workflows/release.yml, not GitHub's
  # auto-generated `archive/refs/tags/` tarball. The uploaded asset is the exact
  # file the release workflow built, checked with `twine check` and covered by a
  # build-provenance attestation; the auto-generated archive is regenerated on
  # demand and is attested by nothing.
  #
  # Three literals to bump together on a release: the tag in the path, the
  # filename, and `version`. They are spelled out rather than interpolated
  # because Homebrew audits the stanza order (url, version, sha256), which leaves
  # nothing to interpolate from at the point the url is written.
  url "https://github.com/hancheng-ai/nextbrief/releases/download/v0.2.1rc1/nextbrief-0.2.1rc1.tar.gz"
  # Declared rather than inferred from the filename: `0.2.1rc1` is exactly the
  # kind of string Homebrew's parser is entitled to read as `0.1.0-rc1`, and the
  # test block compares `version` against what the binary prints.
  version "0.2.1rc1"
  # The markers are the repository-wide "do not sweep this" fence, defined in
  # scripts/bump-version.sh and spelled the same in every file it edits -- an
  # HTML comment inside a Ruby one because the fence is a contract with that
  # script, not with a markup language. It has to be here, and it is the whole
  # reason this line can be trusted: `sha256-of:` names a *past* release, and
  # the sweep is an unbounded replace of the previous version string. Bump 0.2.1rc1
  # to 0.3.0 with this line reading `sha256-of: 0.2.1rc1` and the sweep rewrites it
  # to `0.3.0` while the digest below stays 0.2.1rc1's -- the comment then agrees
  # with `version`, the test two paragraphs down stops looking, and the README
  # goes on advertising a `brew install` that fails its checksum. That is this
  # exact defect, restored to being invisible, one release after it was fixed.
  # <!-- bump-version:skip:begin -->
  # sha256-of: 0.2.0
  # <!-- bump-version:skip:end -->
  #
  # Which release this digest was actually taken from, written down because it
  # cannot be derived and has already gone wrong. `version` is swept by
  # scripts/bump-version.sh; the digest cannot be, because it belongs to a file
  # that does not exist until the tag is pushed and the release job has built
  # it. So the two drift apart on every bump and come back together in a second
  # commit -- which was skipped four releases running, leaving this stanza
  # pointing at a `0.2.1rc1rc*` tarball with the 0.1.0rc14 digest. Nothing said so:
  # the only check on this line asserted that it was sixty-four hex characters,
  # which a four-release-old digest is.
  #
  # That second commit is no longer a thing anyone has to remember: the
  # `homebrew` job in .github/workflows/release.yml reads the digest out of the
  # release's own SHA256SUMS and opens a pull request setting both lines. What
  # it does by hand, if it ever has to be done by hand:
  #
  #   V=0.2.1rc1
  #   gh release view "v$V" --json assets \
  #     --jq '.assets[]|select(.name|endswith(".tar.gz")).digest|sub("^sha256:";"")'
  #
  # then update this line and the `sha256-of:` above it together. The `sub` is
  # not decoration: `.digest` is `sha256:<hex>`, and pasted whole it produces
  # `sha256 "sha256:210a..."` -- which is not sixty-four hex characters, so the
  # one check this line already had would have caught it. The release job reads
  # SHA256SUMS instead, where the digest is bare.
  #
  # While the two disagree, the pinned install is not documented anywhere --
  # tests/test_docs_consistency.py holds both ends of that, so restoring the
  # command means fixing the digest first.
  sha256 "e99595e7edd6e09cc8bd2d97f6a9f3dbe48356887d63d8adf9a0f4b7ff6aa1b2"
  license "Apache-2.0"
  head "https://github.com/hancheng-ai/nextbrief.git", branch: "main"

  # Deliberately silent while the newest release is a prerelease: `github_latest`
  # reads the `/releases/latest` endpoint, which skips prereleases. That is the
  # behaviour we want -- an rc should not arrive on someone's machine because
  # `brew upgrade` found it.
  livecheck do
    url :stable
    strategy :github_latest
  end

  depends_on "python@3.12"

  def install
    # A zipapp, not a libexec virtualenv, because there is nothing here for a
    # virtualenv to isolate: nextbrief declares zero runtime dependencies. A venv
    # would add a directory tree, a pip invocation and a PEP 517 build-backend
    # download over the network at install time, all to produce the same
    # importable code that `zipapp` produces from the standard library alone,
    # offline, in one step.
    #
    # This is safe for this package specifically: it reads its own locales,
    # prompts and templates through importlib.resources rather than through
    # __file__, precisely so that they resolve from inside an archive. See the
    # module docstring in src/nextbrief/resources.py.
    python = Formula["python@3.12"].opt_bin/"python3.12"

    # An explicit __main__.py rather than `--main`. The shim zipapp generates for
    # `--main pkg:func` calls the function and throws the return value away, so
    # every exit code becomes 0: `check` could never report 3, and a scheduler
    # running `nextbrief check || nextbrief run` would never re-run. Staging the
    # entry point by hand is the only way to get `sys.exit(main())` into it.
    # scripts/build-zipapp.sh does the same thing for the released artifact.
    (buildpath/"src/__main__.py").write <<~PY
      import sys

      from nextbrief.cli import main

      sys.exit(main())
    PY
    system python, "-m", "zipapp", "src",
           "--compress",
           "--output", "nextbrief.pyz"
    libexec.install "nextbrief.pyz"

    # An explicit shim rather than a shebang baked into the archive, so the
    # interpreter is the one this formula depends on and cannot be swapped out by
    # whatever `python3` happens to resolve to in the caller's PATH. That matters
    # more than usual here: nextbrief's whole reason for existing is a scheduled
    # run started by a GUI launcher with a nearly empty PATH.
    (bin/"nextbrief").write <<~SH
      #!/bin/sh
      exec "#{python}" "#{libexec}/nextbrief.pyz" "$@"
    SH
    (bin/"nextbrief").chmod 0755

    doc.install "README.md", "README.zh.md", "CHANGELOG.md"
  end

  test do
    # 0. Fence the test off from the machine it runs on, before anything runs.
    #    `nextbrief init` writes a pointer file at $XDG_CONFIG_HOME/nextbrief/
    #    workspace (falling back to ~/.config), and that pointer is how every
    #    later invocation finds the workspace. Unredirected, `brew test` would
    #    repoint a real user's daily brief at Homebrew's scratch directory --
    #    silently, and only discovered the next morning when the nightly run
    #    reports on nothing.
    ENV["HOME"] = testpath
    ENV["XDG_CONFIG_HOME"] = testpath/"config"

    # 1. The binary reports the version this formula claims to have installed.
    #    A stale bottle, or a url pinned to the wrong tag, shows up here and
    #    essentially nowhere else.
    output = shell_output("#{bin}/nextbrief --version").strip
    if build.head?
      assert_match(/\Anextbrief \d+\.\d+\.\d+/, output)
    else
      assert_equal "nextbrief #{version}", output
    end

    # 2. The program actually runs. `v0` is stage 1 plus stage 3 with no model
    #    at all, so it needs no network, no API key and no configuration beyond
    #    the workspace `init` just wrote -- and it exercises the sensing pass,
    #    the four gates and both renderers. A package that imports cleanly but
    #    cannot read its own bundled locales, prompts or templates (the exact
    #    failure mode a zipapp risks) fails right here rather than on a user's
    #    first run.
    system bin/"nextbrief", "init", testpath, "-y", "--no-scan"
    assert_path_exists testpath/"registry.jsonc"

    # --no-notify because a passing test must not put a banner on someone's
    # screen. `v0` forwards unrecognised arguments to the render stage, which is
    # where the flag lives.
    system bin/"nextbrief", "--workspace", testpath, "v0", "--no-notify"
    assert_path_exists testpath/"BRIEF.md"
    assert_path_exists testpath/"BRIEF.html"
    assert_match "nextbrief render", (testpath/"BRIEF.md").read

    # Failure has to be asserted too, not just success. The released zipapp once
    # shipped with an entry point that discarded main()'s return value, so every
    # error exited 0 while every happy path still worked -- a test that only
    # checks success paths passes a build that can never report a failure.
    #
    # `shell_output` with an expected status fails the test if the status differs,
    # so these two lines are what would have caught it.
    shell_output("#{bin}/nextbrief --workspace /nonexistent/nope ls 2>&1", 2)

    rm testpath/"state/snapshot.json"
    shell_output("#{bin}/nextbrief --workspace #{testpath} check 2>&1", 3)
  end
end
