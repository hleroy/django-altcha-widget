---
name: releasing
description: How django-altcha-widget releases work — the pypi-release.yml job chain and its ordering constraints, what scripts/check_dist.py and scripts/release_notes.py enforce, the three hand-edited versions --expect-version reconciles, and the CHANGELOG.md heading convention the release gate depends on. Use when cutting a release, editing the release workflow, or changing what a built distribution must contain.
---

# Releasing

`pypi-release.yml` fires on a `v*.*.*` tag and runs four jobs in a chain: the
full test matrix (called from `run-unit-tests.yml`, not duplicated) → build +
`scripts/check_dist.py` → PyPI upload → GitHub release. Things that follow from
that shape:

- **The GitHub release depends on the upload**, not just on the build, so it
  cannot announce a version that never reached PyPI. On `workflow_dispatch` the
  publish job is skipped by its `refs/tags/` condition, which skips the release
  job with it and leaves the run as a build check.
- **`scripts/check_dist.py` hardcodes what a distribution must contain**: the
  Python modules, the template, `LOCAL_ASSETS`, and the `license-files` entries.
  Keep those in step with `pyproject.toml`. The vendored assets are the
  exception — `STATIC_FILES` derives them from `CHECKSUMS`, so only a *new*
  hand-written static file means editing the constants there.
- **`SDIST_FILES` is coupled to `MANIFEST.in`, and the coupling is not obvious.**
  setuptools carries `tests/test*.py` into the sdist through a distutils default,
  so listing a test module there proves nothing — it would be present anyway.
  What the sdist needs to be *runnable* is `tests/__init__.py`,
  `tests/settings.py` and `tests/urls.py`, and those reach it only through
  `MANIFEST.in`. All three are asserted. Adding a fixture or a settings module the
  suite imports means editing both files. The pytest configuration is the
  exception: it lives in `pyproject.toml`, which the sdist carries
  unconditionally.
- **`--expect-version` is the only thing reconciling the three hand-edited
  versions**: the tag, `[project].version`, and `__version__` in
  `src/django_altcha_widget/__init__.py`. The workflow passes it only on tag
  pushes, since `workflow_dispatch` has no version to check against.
- **`uv.lock` records the project's own version, so a bump means running
  `uv lock` too** — and nothing reconciles it, so this is the one place a
  version bump breaks *everything* rather than the release. The lock carries a
  `[[package]]` entry for `django-altcha-widget` itself, `source = { editable =
  "." }`, whose `version` is copied from `[project].version`. `run-unit-tests.yml`
  sets `UV_LOCKED=1`, so a stale entry fails `uv sync` before a single test
  runs, in **every** matrix cell — the symptom is "The lockfile at `uv.lock`
  needs to be updated", not a test failure, and it does not reproduce locally
  unless you pass `--locked`. `--expect-version` never sees it: the lock is not
  in the wheel or the sdist. Verify with `uv lock --check`, which is what CI
  effectively asserts.
- **Both scripts are covered by `tests/test_scripts.py`**, which synthesises
  wheels and sdists rather than building them. A change to what a distribution
  must contain belongs in `check_dist.py`'s constants and in those tests.
  `test_changelog_has_a_section_for_the_current_version` ties `CHANGELOG.md` to
  `__version__`, so bumping the version without a changelog section fails the
  suite, not just the release.
- **Release notes come from `CHANGELOG.md`** via `scripts/release_notes.py`,
  which fails when the tagged version has no section, when the section is empty,
  or when the heading still says `(unreleased)`. Both scripts are stdlib-only and
  run on the runner's `python3` rather than the virtualenv.
- **`release_notes.py` runs twice, and the first run is the point** — but it is
  a smaller point than it looks, so do not credit the gate with more than it
  does. The build job runs it on tag pushes and throws the output away; only
  `create-gh-release` keeps it. By the time the gate runs, a *missing* section
  has already failed the run twice over: the `test` job requires a section for
  `__version__`, and `--expect-version` ties the tag to `__version__`. What only
  the gate catches is a section that exists but is still headed `(unreleased)`,
  or one that is empty. The suite cannot cover either — `(unreleased)` is the
  correct state for the version under development, so
  `test_changelog_has_a_section_for_the_current_version` checks only that a
  section exists, and nothing but a tag distinguishes the two cases. Narrow as
  that is, it still has to happen *before* `pypi-publish` rather than in the job
  after it, because PyPI can yank a version but never replace it. Keep the gate
  ahead of the upload when touching the release workflow.
- **`release_notes.py` converts nothing.** `CHANGELOG.md` is Markdown and GitHub
  renders release notes as Markdown, so a section is published exactly as
  written; the script only chooses which lines to publish and when to refuse.
  What it does depend on is the heading level: `## ` separates releases, so
  `#` is the file title, `###` is a subsection inside a release, and the Keep a
  Changelog preamble above the first `##` belongs to no section and is never
  published. Do not demote a release heading to `###` or promote a subsection
  to `##`.
- **The heading is `## v1.0.0 (2026-08-09)`, not Keep a Changelog's
  `## [1.0.0] - 2026-08-09`.** The preamble cites that format, but the literal
  convention is not followed, deliberately: the release gate keys on the word
  `(unreleased)` *inside a versioned heading*, which is what separates "you
  forgot to set the date" from "there is no such section". Keep a Changelog's
  free-standing `## [Unreleased]` carries no version, so
  `test_changelog_has_a_section_for_the_current_version` — which requires the
  version under development to already have its own section — could never pass.
