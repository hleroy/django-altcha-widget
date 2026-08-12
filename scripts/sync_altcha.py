#!/usr/bin/env python
"""
Vendor the ALTCHA front-end assets pinned in package.json.

The ALTCHA widget is vendored into
``src/django_altcha_widget/static/django_altcha_widget/altcha/`` so that
``pip install django-altcha-widget`` works without a JavaScript toolchain. package.json
pins the version so that Dependabot can propose upgrades; this script turns such
a proposal into the matching asset update.

Only the modular build is vendored. It is the one the widget serves, because a
strict Content-Security-Policy is the single supported mode; ``dist/main/`` would
be a bundle nothing can reach.

    python scripts/sync_altcha.py            # vendor the pinned version
    python scripts/sync_altcha.py --check    # verify the tree, offline

Assets are downloaded from the npm registry and each one is verified, byte for
byte, against the matching git tag served by jsDelivr. Publishing to npm and
tagging on GitHub are separate actions by the upstream maintainer, so agreement
between the two is a meaningful check that neither has been tampered with.
"""

import argparse
import hashlib
import io
import json
import re
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path
from pathlib import PurePosixPath

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_ROOT = (
    REPO_ROOT / "src" / "django_altcha_widget" / "static" / "django_altcha_widget"
) / "altcha"
PACKAGE_JSON = REPO_ROOT / "package.json"
CHECKSUMS = STATIC_ROOT / "CHECKSUMS"
VENDOR_JSON = STATIC_ROOT / "VENDOR.json"

NPM_TARBALL_URL = "https://registry.npmjs.org/altcha/-/altcha-{version}.tgz"
JSDELIVR_URL = "https://cdn.jsdelivr.net/gh/altcha-org/altcha@v{version}/{path}"
TAG_ARCHIVE_URL = (
    "https://github.com/altcha-org/altcha/archive/refs/tags/v{version}.tar.gz"
)

# Path in the upstream repository -> path under STATIC_ROOT.
ASSETS = {
    # MIT requires the copyright and permission notice to travel with the code
    # we redistribute, so the licence is vendored alongside the assets and
    # re-fetched on every sync rather than transcribed.
    "LICENSE.txt": "LICENSE.txt",
    "dist/external/altcha.min.js": "external/altcha.min.js",
    "dist/external/altcha.css": "external/altcha.css",
    "dist/workers/pbkdf2.js": "workers/pbkdf2.js",
    "dist/workers/sha.js": "workers/sha.js",
    "dist/workers/argon2id.js": "workers/argon2id.js",
    "dist/workers/scrypt.js": "workers/scrypt.js",
}

# Every i18n file is vendored except the regional bundles, which sit between the
# per-language files and the combined all.js without being much use.
I18N_EXCLUDED = {"africa.js", "americas.js", "asia.js", "europe.js"}

# Provenance recorded alongside the assets. The upstream paths are the keys of
# ASSETS collapsed to the directories a reader cares about; version, copyright,
# source and purl are filled in from the sync.
VENDOR_METADATA = {
    "name": "altcha",
    "license": "MIT",
    "homepage": "https://altcha.org",
    "repository": "https://github.com/altcha-org/altcha",
    "vendored": {
        "LICENSE.txt": "LICENSE.txt",
        "external/altcha.min.js": "dist/external/altcha.min.js",
        "external/altcha.css": "dist/external/altcha.css",
        "workers/": "dist/workers/",
        "i18n/": "dist/i18n/ (regional bundles excluded)",
    },
    "notes": (
        "Only the modular build is vendored; django-altcha-widget serves it "
        "under a strict Content-Security-Policy. Run `just sync-altcha` to "
        "re-vendor; see scripts/sync_altcha.py."
    ),
}

# Largest asset this script will read out of the tarball. Members are read into
# memory, and a tar header can claim any size at all, so a tampered-with archive
# would otherwise be free to exhaust it. The biggest real asset is the modular
# bundle at ~80 KB, so this leaves room for upstream to grow considerably.
MAX_ASSET_SIZE = 8 * 1024 * 1024

# Largest response `download` will hold. The npm tarball is the biggest thing
# fetched, at well under a megabyte compressed.
MAX_DOWNLOAD_SIZE = 32 * 1024 * 1024


def fail(message):
    sys.exit(f"error: {message}")


def is_safe_member_path(path):
    """
    Return True if `path` is a plain relative path, safe to join onto a
    destination directory.

    The i18n assets are picked up by pattern rather than named in ASSETS, so
    their path comes from the tarball rather than from this file: a member named
    ``dist/i18n/../../../x.js`` otherwise passes the filter and is written
    outside the repository. A tarball tampered with is the case this script
    exists to catch, and it runs on a machine that can commit to the project.
    """
    pure = PurePosixPath(path)
    return not pure.is_absolute() and ".." not in pure.parts


def get_pinned_version():
    """Return the exact altcha version pinned in package.json."""
    data = json.loads(PACKAGE_JSON.read_text())
    version = data.get("dependencies", {}).get("altcha")
    if not version:
        fail(f"no altcha dependency found in {PACKAGE_JSON}")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        fail(
            f"altcha must be pinned to an exact version in {PACKAGE_JSON}, "
            f"got {version!r}"
        )
    return version


def download(url):
    with urllib.request.urlopen(url) as response:  # noqa: S310
        # Bounded: the response goes straight into memory, and its length is
        # whatever the far end decides to send.
        content = response.read(MAX_DOWNLOAD_SIZE + 1)
    if len(content) > MAX_DOWNLOAD_SIZE:
        fail(f"{url} returned more than {MAX_DOWNLOAD_SIZE} bytes")
    return content


def read_member(tar, member):
    """Read a tarball member into memory, refusing an implausibly large one."""
    if member.size > MAX_ASSET_SIZE:
        fail(f"{member.name} is {member.size} bytes, over the {MAX_ASSET_SIZE} limit")
    return tar.extractfile(member).read(MAX_ASSET_SIZE)


def get_npm_assets(version):
    """Return a mapping of upstream repository path to file content."""
    print(f"-> Downloading altcha {version} from the npm registry")
    tarball = download(NPM_TARBALL_URL.format(version=version))

    assets = {}
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            # Members are prefixed with "package/", mirroring the repository.
            prefix = "package/"
            if not member.name.startswith(prefix):
                continue
            path = member.name[len(prefix) :]
            if path in ASSETS:
                assets[path] = read_member(tar, member)
            elif (
                path.startswith("dist/i18n/")
                and path.endswith(".js")
                and Path(path).name not in I18N_EXCLUDED
            ):
                if not is_safe_member_path(path):
                    fail(f"unsafe path in the npm package: {path}")
                assets[path] = read_member(tar, member)

    missing = set(ASSETS).difference(assets)
    if missing:
        fail(f"missing from the npm package: {', '.join(sorted(missing))}")
    return assets


def verify_against_tag(version, assets):
    """Check each asset against the upstream git tag, as served by jsDelivr."""
    print(f"-> Verifying {len(assets)} files against the v{version} tag")
    for path, content in sorted(assets.items()):
        url = JSDELIVR_URL.format(version=version, path=path)
        if hashlib.sha256(download(url)).digest() != hashlib.sha256(content).digest():
            fail(f"{path} differs between the npm package and the v{version} tag")


def write_assets(assets):
    """
    Write the assets into the static directory, returning the paths written
    relative to STATIC_ROOT.

    Everything else under that directory is removed. The sync owns the whole
    vendored tree, so a file it did not just write is a leftover from an earlier
    version — one upstream renamed, or dropped. Left in place it would be
    checksummed as though it belonged to the version now pinned, pass
    ``--check``, and ship in the wheel as a required asset that was never
    verified against upstream.
    """
    targets = dict(ASSETS)
    for path in assets:
        if path.startswith("dist/i18n/"):
            targets[path] = path[len("dist/") :]

    static_root = STATIC_ROOT.resolve()
    written = set()
    for path, target in sorted(targets.items()):
        destination = STATIC_ROOT / target
        # Belt and braces with the filter in get_npm_assets: nothing is written
        # outside the vendored tree, whatever the tarball called its members.
        if not destination.resolve().is_relative_to(static_root):
            fail(f"{path} would be written outside {STATIC_ROOT.name}/")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(assets[path])
        written.add(target)

    remove_stale(written)
    print(f"-> Wrote {len(written)} files to {STATIC_ROOT.relative_to(REPO_ROOT)}")
    return written


def remove_stale(written):
    """Delete everything under STATIC_ROOT that this sync did not write."""
    for relative, path in iter_vendored_files():
        if relative not in written:
            print(f"   removing {relative}")
            path.unlink()

    # Deepest first, so a subdirectory upstream stopped shipping does not linger
    # as an empty one. A child path always sorts after its parent, so reversing
    # empties the nested directory before the one holding it.
    for directory in sorted(STATIC_ROOT.rglob("*"), reverse=True):
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()


def get_copyright(license_text):
    """Return the copyright line of the upstream licence."""
    match = re.search(r"^Copyright \(c\).*$", license_text, re.MULTILINE)
    if not match:
        fail("no copyright line found in the upstream LICENSE.txt")
    return match.group().strip()


def update_vendor_metadata(version, assets):
    """Point VENDOR.json at the newly vendored version."""
    # Written whole rather than patched, so a fresh checkout needs no seed file
    # and VENDOR_METADATA above is the single description of what is vendored.
    previous = None
    if VENDOR_JSON.exists():
        previous = json.loads(VENDOR_JSON.read_text()).get("copyright")

    metadata = dict(VENDOR_METADATA)
    metadata["version"] = version
    metadata["copyright"] = get_copyright(assets["LICENSE.txt"].decode())
    metadata["source"] = TAG_ARCHIVE_URL.format(version=version)
    metadata["purl"] = f"pkg:npm/altcha@{version}"
    VENDOR_JSON.write_text(json.dumps(metadata, indent=2) + "\n")
    print(f"-> Updated {VENDOR_JSON.relative_to(REPO_ROOT)}")

    if previous != metadata["copyright"]:
        print(
            f"\nnote: the upstream copyright changed to {metadata['copyright']!r}.\n"
            f"      Update the License section of README.md to match."
        )


def iter_vendored_files():
    """
    Yield every vendored asset, as a (relative path, path) pair.

    Everything under STATIC_ROOT comes from upstream: this package's own
    ``altcha-workers.js`` sits a directory above, which is what lets the whole
    tree be checksummed without a carve-out.
    """
    for path in sorted(STATIC_ROOT.rglob("*")):
        if not path.is_file() or path in (CHECKSUMS, VENDOR_JSON):
            continue
        yield path.relative_to(STATIC_ROOT).as_posix(), path


def write_checksums(version, written):
    """Record a SHA-256 for each file this sync wrote."""
    # Taken from what was written rather than from whatever is on disk, so a
    # file this sync did not produce cannot be recorded as part of the vendored
    # version. `remove_stale` should have left the two identical; if it did not,
    # `--check` reports the difference instead of blessing it.
    lines = [f"# altcha {version} - generated by scripts/sync_altcha.py\n"]
    for relative in sorted(written):
        digest = hashlib.sha256((STATIC_ROOT / relative).read_bytes()).hexdigest()
        lines.append(f"{digest}  {relative}\n")
    CHECKSUMS.write_text("".join(lines))
    print(f"-> Wrote {CHECKSUMS.relative_to(REPO_ROOT)}")


def check_vendor_metadata(version):
    """Return the errors found comparing VENDOR.json to the pinned version."""
    if not VENDOR_JSON.exists():
        return [f"{VENDOR_JSON.relative_to(REPO_ROOT)} is missing"]

    declared = json.loads(VENDOR_JSON.read_text())["version"]
    if declared == version:
        return []
    return [
        f"{VENDOR_JSON.relative_to(REPO_ROOT)} declares version {declared}, "
        f"package.json pins {version}"
    ]


def read_checksums():
    """Return the recorded checksums, keyed by path relative to STATIC_ROOT."""
    recorded = {}
    for line in CHECKSUMS.read_text().splitlines():
        if line and not line.startswith("#"):
            digest, _, relative = line.partition("  ")
            recorded[relative] = digest
    return recorded


def check_checksums():
    """Return the errors found comparing the vendored files to CHECKSUMS."""
    if not CHECKSUMS.exists():
        return [f"{CHECKSUMS.relative_to(REPO_ROOT)} is missing"]

    recorded = read_checksums()
    actual = {
        relative: hashlib.sha256(path.read_bytes()).hexdigest()
        for relative, path in iter_vendored_files()
    }

    errors = []
    for relative in sorted(set(recorded) | set(actual)):
        if relative not in actual:
            errors.append(f"{relative} is recorded but missing from static/")
        elif relative not in recorded:
            errors.append(f"{relative} is not recorded in CHECKSUMS")
        elif recorded[relative] != actual[relative]:
            errors.append(f"{relative} does not match its recorded checksum")
    return errors


def check():
    """Verify the vendored tree matches package.json, without network access."""
    version = get_pinned_version()
    errors = check_vendor_metadata(version) + check_checksums()

    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        sys.exit(
            f"\nThe vendored assets are out of sync with package.json (altcha "
            f"{version}).\nRun: python scripts/sync_altcha.py"
        )

    print(f"OK: vendored assets match altcha {version}")


def sync():
    version = get_pinned_version()
    if not shutil.which("npm"):
        print("note: npm is not required, assets come from the registry directly")
    assets = get_npm_assets(version)
    verify_against_tag(version, assets)
    written = write_assets(assets)
    update_vendor_metadata(version, assets)
    write_checksums(version, written)
    print(f"\nDone. Review `git diff` and commit the altcha {version} assets.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the vendored assets match package.json, without downloading",
    )
    if parser.parse_args().check:
        check()
    else:
        sync()


if __name__ == "__main__":
    main()
