---
name: upgrades
description: Procedures for bumping a pinned version in django-altcha-widget — adding or removing a Django version from the test matrix, completing an ALTCHA vendored-asset bump, Python dependency and Python version changes, and the pinned ruff/uv/GitHub Actions versions. Use when handling a Dependabot proposal or changing anything pinned.
---

# Upgrade processes

## Django (adding a version to the matrix)

The Django version appears in **four** places that nothing reconciles
automatically. Adding, say, 6.2 means editing all four:

1. `pyproject.toml` → `[dependency-groups]`: a new `django62` group.
2. `pyproject.toml` → `[tool.uv].conflicts`: add `{ group = "django62" }` to the
   existing list. All Django groups belong in **one** conflict list.
3. `pyproject.toml` → classifiers: `Framework :: Django :: 6.2`.
4. `.github/workflows/run-unit-tests.yml` → `matrix.django-version`.

Then `uv lock` and commit the result. `just pin-django 6.2` maps the version
onto the `django62` group by stripping the dot.

Raising the floor is the same edit in reverse, plus the `Django>=X.Y`
requirement in `[project].dependencies`. Every group must stay inside that
floor; nothing enforces it.

Dependabot proposes Django bumps but will **not** add a matrix cell, so a
proposal that moves past the newest group needs this done by hand.

## ALTCHA (the vendored front end)

Dependabot's `npm` ecosystem proposes a `package.json` bump. A bump on its own
leaves the pin and the assets disagreeing, and `just check-altcha` fails CI until
they agree, which is the point. To complete one:

```bash
just sync-altcha   # downloads, cross-verifies, rewrites CHECKSUMS + VENDOR.json
just check-altcha
git diff --stat    # review before committing
```

Two things the sync tells you but does not do for you: it prints a note when
upstream's copyright line changes, which means editing the License section of
`README.md` to match, and it removes every file upstream no longer ships — a dropped
translation is a silent behaviour change for any project naming one. Read the
removals in `git diff --stat` rather than skimming past them. A **major** version
needs the full checklist at the end of the *ALTCHA is vendored, and strict CSP is
the only mode* section of `CLAUDE.md`, which also explains why the vendoring
machinery is shaped the way it is — read it before touching any of it.

## Python dependencies

`uv.lock` is committed and covers development only — it has no bearing on what
installers of the published package resolve, which is governed by
`[project].dependencies`. After changing any dependency, run `uv lock` and
commit it. CI syncs with `UV_LOCKED=1` and fails on a stale lock.

Dev tooling lives in `[dependency-groups]`, not in an extra, so it stays out of
the published wheel metadata. Keep it that way.

## Python versions

`requires-python` in `pyproject.toml`, the `Programming Language :: Python`
classifiers, and `matrix.python-version` in the test workflow. The floor should
track the Pythons supported by the lowest Django in the matrix.

## Pinned tooling

- **ruff** is pinned exactly in the `test-base` dependency group, and that is
  the *only* place. Minor releases change formatter output and lint defaults,
  which otherwise turns CI red on an unrelated commit. `.pre-commit-config.yaml`
  deliberately runs `.venv/bin/ruff` as a local hook rather than pulling
  `ruff-pre-commit` at a `rev:`, so bumping this one line is the whole job — do
  not "helpfully" add the upstream hook back.
- **The pre-commit hooks** pin `pre-commit-hooks`, `django-upgrade` and
  `conventional-pre-commit` by `rev:`.
  `pre-commit autoupdate` bumps both; nothing proposes it, as no Dependabot
  ecosystem covers them. `django-upgrade`'s `--target-version` tracks the
  `Django>=X.Y` **floor**, not the newest matrix cell — raising it to a version
  above the floor rewrites code the lowest cell still has to run.
- **uv** is pinned via `UV_VERSION` in **both** workflows. Dependabot's
  `github-actions` ecosystem updates the action SHA but **not** a `version:`
  input, so this is a manual bump.
- **GitHub Actions** are pinned by commit SHA with the tag in a trailing
  comment. Keep both in step.
