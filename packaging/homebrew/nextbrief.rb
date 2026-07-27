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
# Fill in the checksum from the published release asset, then push:
#
#   curl -fsSLO https://github.com/hancheng-ai/nextbrief/releases/download/v0.1.0/nextbrief-0.1.0.tar.gz
#   shasum -a 256 nextbrief-0.1.0.tar.gz     # paste over REPLACE_WITH_RELEASE_SHA256
#   cd homebrew-tap
#   git add Formula/nextbrief.rb && git commit -m "nextbrief 0.1.0" && git push
#
# Install and verify from the tap:
#
#   brew tap <owner>/tap
#   brew install nextbrief
#   brew test nextbrief
#   brew audit --strict --online <owner>/tap/nextbrief
#
# Before the tap exists, or before a release is tagged, the head stanza already
# works from this file alone:
#
#   brew install --HEAD --build-from-source ./packaging/homebrew/nextbrief.rb

class Nextbrief < Formula
  desc "Daily brief across your projects, gated on evidence that has to resolve"
  homepage "https://github.com/hancheng-ai/nextbrief"

  # The sdist uploaded by .github/workflows/release.yml, not GitHub's
  # auto-generated `archive/refs/tags/` tarball. The uploaded asset is the exact
  # file the release workflow built, checked with `twine check` and covered by a
  # build-provenance attestation; the auto-generated archive is regenerated on
  # demand and is attested by nothing.
  url "https://github.com/hancheng-ai/nextbrief/releases/download/v0.1.0/nextbrief-0.1.0.tar.gz"
  sha256 "REPLACE_WITH_RELEASE_SHA256" # REPLACE_WITH_RELEASE_SHA256 -- shasum -a 256 of the tarball above
  license "Apache-2.0"
  head "https://github.com/hancheng-ai/nextbrief.git", branch: "main"

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
    system python, "-m", "zipapp", "src",
           "--main", "nextbrief.cli:main",
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

    system bin/"nextbrief", "--workspace", testpath, "v0"
    assert_path_exists testpath/"BRIEF.md"
    assert_path_exists testpath/"BRIEF.html"
    assert_match "nextbrief render", (testpath/"BRIEF.md").read
  end
end
