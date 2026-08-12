# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

`django-altcha-widget` — a Django form field and widget for the ALTCHA
proof-of-work CAPTCHA. The ALTCHA front-end library **is vendored**, and only
its modular build, which the widget always serves. See *ALTCHA is vendored, and
strict CSP is the only mode* below before touching anything asset-related.

The package lives at `src/django_altcha_widget/` — a **src layout**, so it is
not importable from the repository root and tests resolve it through the
editable install `uv sync` creates. Two consequences:

- `[tool.setuptools.packages.find]` needs `where = ["src"]`. Without it the
  build silently produces a wheel with no package in it.
- **A wheel and an sdist disagree about the path.** The wheel holds
  `django_altcha_widget/…`; the sdist mirrors the repository, so the same file
  is at `src/django_altcha_widget/…`. `scripts/check_dist.py` keeps `SRC_DIR`
  for exactly this, and its `STATIC_FILES` are wheel paths.

## Commands

Development runs on [uv](https://docs.astral.sh/uv/), driven through
[just](https://just.systems). `just` alone lists every recipe, and
`PYTHON_VERSION` / `DJANGO_VERSION` select a matrix cell.

Run `just check` and `just test` before considering a change done.

## ALTCHA is vendored, and strict CSP is the only mode

**There is one installation path**: `pip install`, `collectstatic`, done. ALTCHA
lives under
`src/django_altcha_widget/static/django_altcha_widget/altcha/`, committed to the
repository, and the widget always serves it. There is **no** setting naming
where the assets come from and **no** switch selecting a build. This replaced an
earlier un-vendored design whose three installation modes (bundler / self-hosted
/ CDN) crossed with two builds produced ~200 lines of branching README; do not
reintroduce `ALTCHA_DIST_URL` or `ALTCHA_STRICT_CSP` without being asked.

**Only the modular build is vendored.** `dist/main/altcha.min.js` inlines its
stylesheet and instantiates workers from a `blob:` URL, which is exactly what a
strict CSP forbids — under this design nothing could ever serve it, so shipping
it would be ~110 KB of dead weight per wheel. `tests/test_widget.py::
DjangoAltchaWidgetStaticFilesTest::test_the_default_bundle_is_not_vendored`
pins that.

The vendoring machinery, all of which has to stay in step:

- **`package.json`** pins the exact ALTCHA version. It is `"private": true`,
  never published, and not needed to install or use the package. It exists so
  the npm Dependabot ecosystem proposes upgrades.
- **`scripts/sync_altcha.py`** turns such a proposal into assets: it downloads
  the npm tarball and verifies every file byte-for-byte against the same file
  from the upstream git tag served by jsDelivr. npm publishing and GitHub
  tagging are separate maintainer actions, so agreement between them is a real
  tamper check — keep it, along with the tar-traversal guard
  (`is_safe_member_path`) and the `MAX_ASSET_SIZE` / `MAX_DOWNLOAD_SIZE` limits.
  It writes `CHECKSUMS` and `VENDOR.json`.
- **`just check-altcha`** re-verifies offline and runs in CI, so the tree cannot
  drift from the pin.
- **`scripts/check_dist.py` derives `STATIC_FILES` from that same `CHECKSUMS`**,
  which is why vendoring a new asset — or upstream adding a translation — needs
  no edit there. `LOCAL_ASSETS` is the one hand-listed entry.
- **The sync owns everything under `altcha/` unconditionally.**
  `altcha-workers.js` sits a directory *above* it precisely so there is no
  carve-out; do not move it inside. `write_assets` deletes every file it did not
  just write, and `write_checksums` records that same written set rather than
  walking the disk. Both halves matter: a file upstream renamed would otherwise
  survive, be checksummed as part of the newly pinned version, pass
  `just check-altcha`, and ship in the wheel as a required asset — `check_dist`
  derives its list from `CHECKSUMS` — having been verified against nothing.
  `CHECKSUMS` and `VENDOR.json` are exempt via `iter_vendored_files`.
- **`.gitattributes` sets `* -text`, and that is load-bearing.** The assets are
  verified byte-for-byte, so a checkout under `core.autocrlf=true` — the Git for
  Windows default — would rewrite line endings in the vendored JavaScript, fail
  `just check-altcha` wholesale, and build a wheel of altered bytes. It is the
  only line in the file; do not add per-type overrides that re-enable
  normalisation.
- **The static prefix is `django_altcha_widget/altcha/`, not `altcha/`.** The
  app-namespaced prefix is what stops the vendored copy shadowing a project's
  own `static/altcha/`.

The remaining asset setting is **`ALTCHA_TRANSLATIONS`** — a language code or
`"all"`, resolved as `i18n/<value>.js`; `None` means English. Every per-language
file upstream ships is vendored, plus the combined `all.js`, so any of those
works. The four **regional** bundles are not: `I18N_EXCLUDED` in
`scripts/sync_altcha.py` drops them, so `"europe"` resolves to a URL with no file
behind it — a silent 404, or a `ValueError` at render time under
`ManifestStaticFilesStorage`. That exclusion and the README's
`ALTCHA_TRANSLATIONS` section have to stay in step; nothing checks it, because
`static()` invents a URL for any name at all.

What follows from all that:

- **Every asset URL is a constant resolved through `static()`.** The
  `ALTCHA_*_PATH` constants in `conf.py` are the vendored layout, which mirrors
  upstream's `dist/` subpaths one-to-one so `VENDOR.json` stays auditable. There
  is no absolute-URL or CDN passthrough left, and no reason to add one: a CDN is
  not `'self'`.
- **`AltchaField` refuses `required=False`**, with a `ValueError` from
  `__init__`. The two obvious alternatives are both worse: honouring it is a
  silent bypass, since anything posting the form without the field then passes
  unchallenged, and forcing it back to True is an argument quietly ignored. The
  refusal is also what makes the `super().validate()` call in `validate()`
  sufficient — with `required` guaranteed True it catches every empty value, so
  there is no second emptiness check, and adding one back would be dead code.
  `ALTCHA_VERIFICATION_ENABLED` is the supported way to stop verifying.
- **`AltchaField` refuses a `widget` argument**, with a `TypeError` from
  `__init__`. It builds the widget itself and hands it the options it collected
  from `kwargs`, so a caller's instance could only be replaced — the same
  quietly-ignored argument the `required=False` refusal exists to prevent. The
  supported override is the `widget` class attribute on a subclass, which
  `__init__` reads as `self.widget`; that keeps the field in charge of passing
  the options along. `TypeError` rather than `ValueError` because this is an
  argument the field does not take at all, matching how a `name` option and
  every unknown option already fail.
- **`get_assets_context()` and `AltchaWidget.media` no longer branch**, and the
  template has no `{% if %}` around the asset tags. The one guard left is
  `js_translations_url` being truthy. Both entry points build from the same
  context so they cannot disagree.
- **The asset tags must not be `async`.** Module scripts are deferred and
  evaluated in document order, which is the only thing making the worker
  registration run after the widget module. The old non-strict branch used
  `async defer`; that branch is gone, and
  `test_module_scripts_are_not_async` keeps it gone.
- **`altcha-workers.js` is ours, not upstream's.** It exists because ALTCHA's
  modular build registers **no** proof-of-work algorithms — the integrator has
  to populate `$altcha.algorithms`, which is undocumented upstream. It requires
  the `data-altcha-workers` mapping the widget renders and throws without it.
- **Workers are resolved one file at a time** through `WORKER_FILENAMES`, so
  hashed staticfiles storages produce usable URLs; a directory has no manifest
  entry. That is the whole reason the URLs are resolved server-side and passed
  in as JSON rather than being derived in the browser from a known layout. Those
  file names are ALTCHA's, from its `dist/workers/`.
- **The submitted value comes from ALTCHA's own `<input type="hidden">`, not
  from anything this package renders.** `<altcha-widget>` creates it under the
  `name` it is given, in the **light DOM**, so the browser submits it as an
  ordinary form control. The widget template deliberately renders no input of
  its own; `AltchaWidget` subclasses `HiddenInput` only for `is_hidden`. Two
  controls under one name would be resolved by document order alone, and the
  package used to ship exactly that — inherited from django-altcha, which still
  does.

  For the same reason **`name` is deliberately absent from
  `WIDGET_ATTRIBUTES`**, though it is one of ALTCHA's HTML attributes. The
  template renders it from the field's name, so an option of the same name would
  emit a second `name` attribute — invalid HTML, resolved by document order, and
  a CAPTCHA that fails for everyone if the wrong one ever won.
  `get_altcha_options` pops it for a directly-constructed widget, and
  `AltchaField` rejects it with a `TypeError` because it never reaches
  `default_options`. Do not re-add it when reconciling the tuple against a new
  ALTCHA release.

  This is a build-time property of ALTCHA, not a documented API. It is set by
  the shadow-DOM argument of Svelte's custom-element factory: an explicit
  `false` in 2.3.0, simply absent in 3.x — the same behaviour reached two
  different ways across a major. Verify it rather than assume it, by checking
  the shipped bundle for `attachShadow` reached from the `altcha-widget`
  registration and for `attachInternals` (a form-associated element would move
  the value out of reach the same way a shadow root would). If a future ALTCHA
  ever renders it into a shadow root, the field stops receiving any value at
  all and `tests/test_widget.py::DjangoAltchaWidgetFormControlTest` is where
  the expectations live.
- Moving to a new ALTCHA major version means bumping `package.json`, running
  `just sync-altcha`, and then re-checking: the `ASSETS` map in
  `scripts/sync_altcha.py` and the `ALTCHA_*_PATH` constants against the
  published `dist/`, `WORKER_FILENAMES` against `dist/workers/`, the widget
  attributes in `WIDGET_ATTRIBUTES` / `WIDGET_CONFIGURATION`
  (`src/django_altcha_widget/__init__.py`), the light-DOM assumption above, and
  whether the payload still verifies against the `altcha` Python library. Do not
  assume it does — prove it. Note that a sync only fails on an asset that has
  *moved*; one that changed meaning in place syncs silently.

## Upgrade processes

Bumping Django, ALTCHA, a Python dependency, or the pinned tooling: read the
`upgrades` skill first — several of these touch places nothing reconciles
automatically, so a partial edit passes locally and fails in CI or ships wrong.
Cutting a release, or changing the release workflow or what a distribution must
contain: read the `releasing` skill.

<!-- Both procedures live in .claude/skills/{upgrades,releasing}/SKILL.md so they
     load when they apply rather than in every session. -->

## Gotchas

- **Ruff formats Python inside Markdown code blocks** (0.16+). Editing a
  ````python```` block in `README.md` can fail `just check`; run `just valid`.
- **Bumping `[project].version` means running `uv lock`, even though no
  dependency changed.** `uv.lock` holds an entry for the package itself, whose
  `version` mirrors `pyproject.toml`. Everything else here frames relocking as a
  dependency-driven step — DEVNOTES and the `upgrades` skill both say "after
  changing a dependency" — so a version bump is the case that slips through. CI
  syncs with `UV_LOCKED=1`, so the stale lock fails **every** matrix cell at
  `uv sync`, before any test runs, and nothing local reproduces it: `just test`
  passes, because a plain `uv sync` just re-resolves. `uv lock --check` is the
  check that catches it.
- **Ruff honours `.gitignore` and skips ignored files silently.** A file that is
  accidentally gitignored is never linted — this previously hid a lint error in
  `scripts/` for an entire branch. If a file seems immune to `just check`, check
  `.gitignore` first. `.gitignore` is deliberately narrow and anchored for this
  reason: it was trimmed from a generic template that carried `*/local/*` and a
  bare `lib64`, either of which would have taken real source out of `just check`
  without saying so. Keep new patterns anchored, and do not paste a template
  back in.
- **Editing `MANIFEST.in` does not change the sdist until the egg-info is
  cleared.** setuptools caches the file list in
  `src/django_altcha_widget.egg-info/SOURCES.txt` and reuses it, so a build
  after a `MANIFEST.in` edit keeps shipping whatever the previous one found.
  `check_dist.py` will not catch it: it checks that nothing is *missing*, never
  that nothing is extra. Run `just clean` (which reaches `src/*.egg-info/`)
  before trusting an sdist you have just changed the manifest for — and note
  that `just clean` takes the virtualenv with it, so `just dev` follows.
- **The strict-CSP path is the only path, so changes to it need browser
  verification against real CSP headers**, not just a passing unit test. The
  suite asserts on rendered URLs, and `static()` invents a URL for a file that
  does not exist — only
  `DjangoAltchaWidgetStaticFilesTest` notices a missing asset, and nothing at
  all notices a *working* URL serving the wrong bytes.
- **`tests/settings.py` configures no assets**, because a real install
  configures none either. Nothing in it makes the suite unrepresentative; keep
  it that way rather than adding an asset setting to make a test convenient.
- Replay protection keys the cache on the challenge **signature**: under the
  ALTCHA v2 proof-of-work scheme the payload carries a challenge *object*, not
  the hash string the v1 scheme keyed on.

## Conventions

- **Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/)**,
  enforced by `conventional-pre-commit` on the `commit-msg` stage — see
  `.pre-commit-config.yaml`. Allowed types: `feat`, `fix`, `refactor`, `chore`,
  `docs`, `test`, `style`, `perf`, `ci`, `build`, `revert`. `chore` is the one
  Dependabot uses, via the `chore(deps)` and `chore(ci)` prefixes set in
  `.github/dependabot.yml`. Nothing in CI checks this, so the hook is the only
  thing enforcing it: `just hooks` installs it, and it needs all three
  `--hook-type`s, which is why the recipe passes them explicitly.
- Match the surrounding style: comments explain *why*, not *what*.
- Do not add a dependency without a concrete reason — the runtime dependency
  list is deliberately two entries long.
- User-facing changes belong in `CHANGELOG.md` and, when they change how the
  package is used or configured, in `README.md`; developer-facing process
  changes belong in `DEVNOTES.md` and here. `README.md` is the only one of the
  three that ships (as the `readme` in `pyproject.toml`, so it is also the PyPI
  page) — which is why its links are absolute GitHub URLs and `DEVNOTES.md` can
  use relative ones.
