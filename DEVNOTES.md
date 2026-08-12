# Development notes for `django-altcha-widget`

Notes for working on the package itself. Using it in a project needs none of
this — see [README.md](README.md).

- [Getting set up](#getting-set-up)
- [The test matrix](#the-test-matrix)
- [The vendored ALTCHA assets](#the-vendored-altcha-assets)
- [The release pipeline](#the-release-pipeline)

## Getting set up

Tasks are defined in the `justfile` and run with [just](https://just.systems),
on top of [uv](https://docs.astral.sh/uv/):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # or your package manager
uv tool install rust-just
just dev                                          # virtualenv + dev dependencies
```

uv installs the interpreter itself, so a system Python older than the 3.12 floor
is not a problem.

| Recipe | What it does |
|---|---|
| `just dev` | Create the virtualenv and install the development dependencies |
| `just test` | Run the test suite |
| `just cov` | Run the test suite under coverage, reporting the missing lines |
| `just check` | Validate formatting and linting with Ruff |
| `just valid` | Apply Ruff formatting and autofixes |
| `just sync-altcha` | Re-vendor the ALTCHA assets pinned in `package.json` |
| `just check-altcha` | Verify the vendored assets match that pin (runs in CI) |
| `just pin-django 6.0` | Re-sync the virtualenv against another Django version |
| `just dist` | Build the source and wheel distributions |
| `just check-dist` | Verify the built distributions are complete (runs in CI) |
| `just check-dist --expect-version v1.0.0` | The same, plus the tag and `__version__` agreeing |
| `just release-notes v1.0.0` | Preview the release notes taken from `CHANGELOG.md` |
| `just hooks` | Install the git pre-commit hooks (optional) |
| `just hooks-all` | Run the pre-commit and pre-push hooks against the whole tree |
| `just clean` | Remove the virtualenv and build artifacts |

Run `just check` and `just test` before opening a pull request.

`just cov` measures the shipped package only: `[tool.coverage.run].source` names
`django_altcha_widget` rather than a directory, so the suite itself and
`scripts/` stay out of the figure. Leaving the vendoring scripts out understates
what is tested rather than hiding a gap — `tests/test_scripts.py` exercises them
at length. Nothing enforces a threshold, in CI or locally; the report is there to
be read, not to fail a build.

## The pre-commit hooks

`just hooks` installs them. They are optional and CI does not run pre-commit;
the linting and the tests they run are also GitHub Actions jobs, so a
contributor who skips the hooks is caught there instead.

They span three git stages, which is why `just hooks` passes all three
`--hook-type`s — a bare `pre-commit install` wires up only the first, and the
other two would then silently never fire:

| Stage | What runs |
|---|---|
| `pre-commit` | The file checks, `django-upgrade`, ruff, and `check-altcha` |
| `commit-msg` | `conventional-pre-commit` |
| `pre-push` | The test suite, via `just test` |

**Commit messages must follow [Conventional Commits](https://www.conventionalcommits.org/).**
This is the one thing the hooks enforce that CI does not check at all, so it is
also the one that a contributor without the hooks installed can get wrong
unchallenged. The allowed types are the usual set; `chore` covers the
`chore(deps)` and `chore(ci)` prefixes `.github/dependabot.yml` is configured to
use, so Dependabot's pull requests pass unchanged. Note that the history predates
this, and `git log` is mostly prose.

Three adaptations in `.pre-commit-config.yaml` are load-bearing:

- **The vendored ALTCHA tree is excluded wholesale.** Those files are verified
  byte for byte, so a whitespace-trimming hook that "fixes" one of them fails
  `just check-altcha` and builds a wheel of altered bytes — the same failure
  `.gitattributes` (`* -text`) exists to prevent. A local `check-altcha` hook
  runs on every commit as the backstop.
- **Ruff is a local hook, not `ruff-pre-commit`.** It is pinned exactly in the
  `test-base` group; a `rev:` in the hook config would be a second pin, and the
  drift is silent — pre-commit reformats on commit and CI then rejects the
  result. The local hook runs that one pinned binary over the whole tree, which
  also matters because ruff formats Python inside Markdown code blocks and a
  filename-scoped hook would skip `README.md`.
- **`default_stages: [pre-commit]`, plus an explicit `stages:` on three hooks.**
  Without the default, every unstaged hook would run at *all* three stages and
  repeat itself in full on push. `trailing-whitespace`, `end-of-file-fixer` and
  `check-added-large-files` need pinning individually on top of that, because a
  hook's own `stages:` — declared upstream as `[pre-commit, pre-push, manual]`
  for those three — beats `default_stages`.

`pre-commit autoupdate` bumps the three third-party hook revisions. It cannot
touch the ruff pin, which is the point.

## The test matrix

CI runs every combination of Python 3.12/3.13/3.14 and Django 6.0/6.1. The
Python axis comes from `PYTHON_VERSION`, which uv resolves to an interpreter,
downloading one if needed. The Django axis comes from a dependency group per
version in `pyproject.toml`, declared as mutually exclusive under
`[tool.uv].conflicts` so that a single `uv.lock` can hold one resolution per
cell:

```bash
PYTHON_VERSION=3.14 just dev       # newest Python, default Django
just pin-django 6.0                # same interpreter, Django 6.0 group
```

`just pin-django 6.0` maps the version onto the `django60` group. Supporting a
new Django release therefore means adding both a `djangoXY` group and a matrix
entry in `.github/workflows/run-unit-tests.yml`; neither is derived from the
other, and neither is derived from the `Django>=6.0` floor in `dependencies`.

`uv.lock` is committed so that CI and contributors resolve identically. CI syncs
with `UV_LOCKED=1`, which fails the run if the lock has drifted from
`pyproject.toml`. After changing a dependency, run `uv lock` and commit the
result. The lock covers development only — it has no effect on what people who
install the published package get, which is governed by `dependencies`.

## The vendored ALTCHA assets

ALTCHA lives under
`src/django_altcha_widget/static/django_altcha_widget/altcha/`, committed to the
repository so that `pip install` needs no JavaScript toolchain. `package.json`
pins the exact version — it is never published to npm and is not needed to
install or use the package; it exists so that Dependabot proposes upgrades.

`just sync-altcha` turns such a proposal into the matching assets. It downloads
the pinned version from the npm registry and verifies every file, byte for byte,
against the same file served from the upstream git tag by jsDelivr: publishing to
npm and tagging on GitHub are separate actions, so agreement between the two is
a meaningful check that neither was tampered with. It then records a SHA-256 for
each file in `CHECKSUMS` and the provenance in `VENDOR.json`.

`just check-altcha` re-verifies that tree offline and runs in CI, so the vendored
files cannot drift from the pinned version. `scripts/check_dist.py` reads the
same `CHECKSUMS` to confirm every asset reaches the built wheel and sdist.

Only the modular build is vendored — `dist/main/` would be a bundle nothing can
serve, since it inlines its stylesheet and instantiates its Proof-of-Work
workers from a `blob:` URL, which is exactly what the strict CSP the package
promises forbids. That build registers no algorithm on its own, so the workers
have to be declared explicitly: `altcha-workers.js`, one directory above, does
that. It is hand-written and belongs to this package rather than to ALTCHA,
which is why no sync ever touches it.

`I18N_EXCLUDED` in `scripts/sync_altcha.py` drops the four regional translation
bundles — `africa`, `americas`, `asia`, `europe`. They are much heavier than a
single language without being much lighter than `all`, so they save little and
cost a copy of every translation in the wheel. Nothing validates
`ALTCHA_TRANSLATIONS`, so naming one resolves through `static()` like any other
name and fails only at request time; that exclusion and the
`ALTCHA_TRANSLATIONS` warning in `README.md` have to stay in step.

## The release pipeline

Pushing a `v*.*.*` tag runs `.github/workflows/pypi-release.yml`, which chains
four jobs so that each one only happens if the previous one earned it:

1. **test** — the full matrix, called from `run-unit-tests.yml` rather than
   duplicated. A tag that fails on any supported cell never reaches PyPI, where
   a version can be yanked but never replaced.
2. **build** — `uv build`, then `just check-dist`. The static files and the
   template reach the wheel through package-data globs, so they can go missing
   without failing the build or the tests; the check confirms the wheel and the
   sdist carry every vendored ALTCHA asset, `altcha-workers.js`, the widget
   template, the Python modules and the licences — ALTCHA's included, as MIT
   requires. On a tag it also requires the tag, both distribution filenames
   and `__version__` to agree, so a tag pushed before the version bump fails
   here rather than uploading the previous version under the new one's name.
3. **publish** — uploads to PyPI with trusted publishing. It downloads the
   distributions as an artifact instead of rebuilding them, so `id-token: write`
   is held by a job that runs no project code.
4. **release** — creates the GitHub release, with the distributions attached and
   the notes taken from the tagged version's section of `CHANGELOG.md`. It
   depends on the upload, so a release never announces a version that is not
   installable, and it fails if that section is missing or still says
   *unreleased*.

`just release-notes v1.0.0` prints what step 4 would publish. See
[RELEASE.md](RELEASE.md) for the steps that lead up to the tag.
