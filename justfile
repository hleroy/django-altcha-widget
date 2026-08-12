# Development tasks for django-altcha-widget.
# Requires `just` (https://just.systems) and `uv` (https://docs.astral.sh/uv/).

# uv fetches the interpreter if it is not already installed, so a system Python
# older than the floor declared in pyproject.toml is not a problem.
python_version := env_var_or_default("PYTHON_VERSION", "3.13")
# The Django matrix cell to sync against, as a version: see [dependency-groups]
# in pyproject.toml. CI overrides this per matrix cell.
django_version := env_var_or_default("DJANGO_VERSION", "6.1")
venv := ".venv"
python := venv + "/bin/python"
pytest := venv + "/bin/pytest"
ruff := venv + "/bin/ruff"

# List the available recipes
default:
    @just --list

# Create the virtualenv and install the development dependencies
dev:
    @just pin-django {{ django_version }}

# Run the test suite, e.g. `just test -k widget`
test *args:
    @echo "-> Run the test suite"
    {{ pytest }} {{ args }}

# Bare `--cov` takes its scope from [tool.coverage.run] in pyproject.toml, and
# arguments are appended, so `just cov --cov-report=html` also writes htmlcov/.
# Run the test suite under coverage, e.g. `just cov -k widget`
cov *args:
    @echo "-> Run the test suite with coverage"
    {{ pytest }} --cov {{ args }}

# Sync the virtualenv against a Django matrix cell, e.g. `just pin-django 6.0`.
# The version maps onto the matching dependency group: 6.0 -> django60.
pin-django version:
    @echo "-> Sync Python {{ python_version }} with Django {{ version }}"
    uv sync --python {{ python_version }} --group django{{ replace(version, ".", "") }}

# Validate formatting and linting with Ruff
check:
    @echo "-> Run Ruff linter validation (pycodestyle, bandit, isort, and more)"
    {{ ruff }} check
    @echo "-> Run Ruff format validation"
    {{ ruff }} format --check

# Apply Ruff formatting and lint autofixes
valid:
    @echo "-> Run Ruff format"
    {{ ruff }} format
    @echo "-> Run Ruff linter"
    {{ ruff }} check --fix

# All three hook types: the commit-msg and pre-push stages are not installed by
# a bare `pre-commit install`, so conventional commits and the test run would
# silently never fire.
# Install the git pre-commit hooks (optional; CI does not depend on them)
hooks:
    @echo "-> Install the pre-commit hooks"
    {{ venv }}/bin/pre-commit install --hook-type pre-commit --hook-type commit-msg --hook-type pre-push

# Run every pre-commit and pre-push hook against the whole tree
hooks-all:
    {{ venv }}/bin/pre-commit run --all-files
    {{ venv }}/bin/pre-commit run --all-files --hook-stage pre-push

# Vendor the ALTCHA assets pinned in package.json
sync-altcha:
    {{ python }} scripts/sync_altcha.py

# Verify the vendored ALTCHA assets match package.json
check-altcha:
    {{ python }} scripts/sync_altcha.py --check

# Build the source and wheel distributions
dist:
    @echo "-> Build source and wheel distributions"
    uv build

# Check the distributions in dist/, e.g. `just check-dist --expect-version v1.0.0`
check-dist *args:
    @echo "-> Check the built distributions"
    {{ python }} scripts/check_dist.py {{ args }}

# Preview the GitHub release notes for a version, e.g. `just release-notes v1.0.0`
release-notes version:
    @{{ python }} scripts/release_notes.py {{ version }}

# Remove the virtualenv and the build artifacts
clean:
    @echo "-> Clean the Python env"
    # src layout: the egg-info uv builds for the editable install sits under
    # src/, which a bare *.egg-info/ glob does not reach.
    rm -rf {{ venv }} .*_cache/ *.egg-info/ src/*.egg-info/ build/ dist/
    rm -rf .coverage .coverage.* htmlcov/
    find . -type f -name '*.py[co]' -delete -o -type d -name __pycache__ -delete
