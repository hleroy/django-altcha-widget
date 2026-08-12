#!/usr/bin/env python
"""
Check the built distributions before they are uploaded.

    python scripts/check_dist.py              # check ./dist
    python scripts/check_dist.py --dist-dir out
    python scripts/check_dist.py --expect-version v1.0.0

A wheel that ships none of the vendored ALTCHA assets, static files or templates
still imports, and still passes the test suite, because the tests run against
the source tree. It breaks only once someone installs it — and a version
uploaded to PyPI can never be replaced, only yanked. Those files reach the wheel
through the package-data globs in pyproject.toml, so renaming a directory or
adding an asset outside ``static/`` drops files out of the distribution with no
error anywhere. This runs between the build and the upload so that a packaging
mistake fails the release instead of shipping.

``--expect-version`` additionally requires the tag being released, both
distribution filenames and ``django_altcha_widget.__version__`` to agree. All
three are set by hand, in three different files, and nothing else reconciles
them: a tag pushed before the version bump otherwise builds and uploads the
previous version under a release announcing the new one.
"""

import argparse
import re
import sys
import tarfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "django_altcha_widget"
STATIC_PREFIX = f"{PACKAGE}/static/{PACKAGE}"

# src layout: the sdist mirrors the repository, so package members appear under
# src/ there, while a wheel is built from the package itself and never carries
# the prefix. The two archives therefore need different paths for one file.
SRC_DIR = "src"

# The vendored ALTCHA assets, recorded by `just sync-altcha`. Deriving the list
# from CHECKSUMS rather than repeating it here is what keeps vendoring a new
# asset — or upstream adding a translation — from needing an edit in this file.
CHECKSUMS = REPO_ROOT / SRC_DIR / PACKAGE / "static" / PACKAGE / "altcha" / "CHECKSUMS"

# The one static file CHECKSUMS does not cover: altcha-workers.js is ours rather
# than upstream's, so it sits outside the tree a sync owns. That also makes it
# the one asset a packaging mistake could drop without `just check-altcha`
# noticing.
LOCAL_ASSETS = ["altcha-workers.js"]


def vendored_assets():
    """Every shipped static file, as a path relative to the package's static/."""
    if not CHECKSUMS.is_file():
        raise SystemExit(f"{CHECKSUMS} is missing; run `just sync-altcha`")

    paths = []
    for line in CHECKSUMS.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        # `sha256sum` format: digest, two spaces, path.
        _digest, _, path = line.partition("  ")
        if not path:
            raise SystemExit(f"Malformed line in {CHECKSUMS}: {line!r}")
        paths.append(f"altcha/{path}")

    if not paths:
        raise SystemExit(f"{CHECKSUMS} lists no assets")

    return sorted(set(paths) | set(LOCAL_ASSETS))


STATIC_FILES = [f"{STATIC_PREFIX}/{asset}" for asset in vendored_assets()]

# Python modules and the widget template.
WHEEL_FILES = [
    f"{PACKAGE}/__init__.py",
    f"{PACKAGE}/conf.py",
    f"{PACKAGE}/templates/{PACKAGE}/altcha_widget.html",
    *STATIC_FILES,
]

# The files declared in license-files, expected in the wheel metadata. MIT
# requires ALTCHA's notice to travel with the code redistributed here, so its
# licence is expected in the metadata as well as under static/.
# Paths are relative to <name>-<version>.dist-info/licenses/.
WHEEL_LICENSES = [
    "LICENSE",
    f"{SRC_DIR}/{STATIC_PREFIX}/altcha/LICENSE.txt",
]

# The sdist is what a distribution packager builds from, so the tests have to
# be in it, not just the importable package — and they have to be *runnable*.
# setuptools carries `tests/test*.py` in by itself through a distutils default,
# which is why listing a test module alone proved nothing: the three files below
# it reach the sdist only through MANIFEST.in, and without them collection ends
# at `from .test_cache import ...` with no known parent package.
SDIST_FILES = [
    # Also the pytest configuration: DJANGO_SETTINGS_MODULE and pythonpath live
    # in its [tool.pytest.ini_options], so the suite is unrunnable without it.
    "pyproject.toml",
    # The vendoring script and the version it reads: re-vendoring has to be
    # possible from the sdist, which is what a distribution packager builds from.
    "package.json",
    "scripts/sync_altcha.py",
    "tests/__init__.py",
    "tests/settings.py",
    "tests/urls.py",
    "tests/test_widget.py",
    f"{SRC_DIR}/{PACKAGE}/__init__.py",
    *(f"{SRC_DIR}/{name}" for name in STATIC_FILES),
]

# The version is bumped by hand in pyproject.toml and in __init__.py, and the
# tag is typed by hand on top of that. Nothing reconciles the three.
PACKAGE_VERSION = re.compile(r"^__version__\s*=\s*[\"']([^\"']+)[\"']", re.MULTILINE)


def sole_distribution(dist_dir, suffix, errors):
    """The one file in dist_dir with this suffix, or None with an error."""
    matches = sorted(dist_dir.glob(f"*{suffix}"))
    if not matches:
        errors.append(f"No {suffix} found in {dist_dir}/")
        return None
    if len(matches) > 1:
        # Publishing uploads everything in dist/, so a leftover build from an
        # earlier version would be pushed to PyPI along with this one.
        names = ", ".join(path.name for path in matches)
        errors.append(f"Expected one {suffix} in {dist_dir}/, found: {names}")
        return None
    return matches[0]


def report_missing(kind, expected, present, errors):
    errors.extend(
        f"{kind} is missing {name}" for name in expected if name not in present
    )


def check_wheel(wheel, errors):
    names = set(zipfile.ZipFile(wheel).namelist())
    label = f"Wheel {wheel.name}"

    report_missing(label, WHEEL_FILES, names, errors)

    dist_infos = {name.split("/")[0] for name in names if ".dist-info/" in name}
    if len(dist_infos) != 1:
        errors.append(f"{label} has {len(dist_infos)} .dist-info directories")
        return

    licenses = f"{dist_infos.pop()}/licenses"
    expected = [f"{licenses}/{name}" for name in WHEEL_LICENSES]
    report_missing(label, expected, names, errors)


def check_sdist(sdist, errors):
    with tarfile.open(sdist) as archive:
        names = archive.getnames()

    # Everything sits under a single <name>-<version>/ directory.
    roots = {name.split("/")[0] for name in names}
    if len(roots) != 1:
        errors.append(f"Sdist {sdist.name} has {len(roots)} root directories")
        return

    root = roots.pop()
    label = f"Sdist {sdist.name}"
    stripped = {name[len(root) + 1 :] for name in names}

    report_missing(label, SDIST_FILES, stripped, errors)


def distribution_version(path):
    """The version encoded in a wheel or sdist filename."""
    name = path.name
    if name.endswith(".whl"):
        # <name>-<version>-<python>-<abi>-<platform>.whl
        parts = name.split("-")
        return parts[1] if len(parts) > 2 else ""
    stem = name.removesuffix(".tar.gz")
    _name, _, version = stem.rpartition("-")
    return version


def packaged_version(wheel):
    """__version__ as the wheel actually ships it, or None if absent."""
    with zipfile.ZipFile(wheel) as archive:
        try:
            source = archive.read(f"{PACKAGE}/__init__.py").decode()
        except KeyError:
            return None
    match = PACKAGE_VERSION.search(source)
    return match.group(1) if match else None


def check_versions(wheel, sdist, expected, errors):
    """The tag, both filenames and __version__ must all say the same thing."""
    found = {}

    if wheel is not None:
        found[wheel.name] = distribution_version(wheel)
        version = packaged_version(wheel)
        if version is None:
            errors.append(f"No __version__ in {PACKAGE}/__init__.py in {wheel.name}")
        else:
            found[f"{PACKAGE}.__version__"] = version

    if sdist is not None:
        found[sdist.name] = distribution_version(sdist)

    if expected is not None:
        # Tags are written v1.0.0; the version they release is 1.0.0.
        found[f"tag {expected}"] = expected.removeprefix("v")

    if len(set(found.values())) > 1:
        detail = ", ".join(
            f"{source} says {version}" for source, version in found.items()
        )
        errors.append(f"Version mismatch: {detail}")
    elif expected is not None:
        print(
            f"OK: tag, distributions and __version__ all say {found[f'tag {expected}']}"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dist-dir",
        default="dist",
        type=Path,
        help="directory holding the built distributions (default: dist)",
    )
    parser.add_argument(
        "--expect-version",
        metavar="VERSION",
        help=(
            "the version being released, e.g. v1.0.0; checked against both "
            "distribution names and the __version__ inside the wheel"
        ),
    )
    args = parser.parse_args()

    dist_dir = args.dist_dir
    if not dist_dir.is_dir():
        raise SystemExit(f"{dist_dir}/ does not exist; run `just dist` first")

    errors = []
    wheel = sole_distribution(dist_dir, ".whl", errors)
    sdist = sole_distribution(dist_dir, ".tar.gz", errors)

    if wheel is not None:
        check_wheel(wheel, errors)
    if sdist is not None:
        check_sdist(sdist, errors)
    check_versions(wheel, sdist, args.expect_version, errors)

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(f"{len(errors)} problem(s) found; not fit to publish")

    print(f"OK: {wheel.name} and {sdist.name} are complete")


if __name__ == "__main__":
    main()
