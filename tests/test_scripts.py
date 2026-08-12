"""
Tests for the release scripts under scripts/.

These run in the release pipeline, between the build and the upload, where a
false pass ships a broken wheel to PyPI and a false failure blocks a release.
The distributions here are synthesised rather than built: the checks are about
which members an archive holds, so building a real wheel would only make the
suite slower.
"""

import tarfile
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest import mock

from django_altcha_widget import __version__
from scripts import check_dist
from scripts import release_notes
from scripts import sync_altcha

VERSION = "1.0.0"

CHANGELOG = """\
# Changelog

Preamble text, above every release section and part of none of them.

## v2.0.0 (2026-09-01)

Later release, to be stopped at.

## v1.0.0 (2026-08-09)

Intro paragraph, mentioning
[django-altcha](https://github.com/aboutcode-org/django-altcha).

### Features

- `AltchaField` for Django forms.

## v0.9.0 (unreleased)

Never released.
"""


def wheel_members(version=VERSION):
    """Every member a complete wheel is expected to hold."""
    dist_info = f"{check_dist.PACKAGE}-{version}.dist-info"
    return [
        *check_dist.WHEEL_FILES,
        *(f"{dist_info}/licenses/{name}" for name in check_dist.WHEEL_LICENSES),
        f"{dist_info}/METADATA",
    ]


class DistributionTestCase(TestCase):
    """Builds throwaway distributions in a temporary directory."""

    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dist_dir = Path(tmp.name)

    def write_wheel(self, version=VERSION, omit=(), init_version=None):
        path = self.dist_dir / f"{check_dist.PACKAGE}-{version}-py3-none-any.whl"
        init = f"{check_dist.PACKAGE}/__init__.py"
        with zipfile.ZipFile(path, "w") as archive:
            for name in wheel_members(version):
                if name in omit:
                    continue
                if name == init:
                    shipped = init_version if init_version else version
                    archive.writestr(name, f'__version__ = "{shipped}"\n')
                else:
                    archive.writestr(name, "")
        return path

    def write_sdist(self, version=VERSION, omit=()):
        path = self.dist_dir / f"{check_dist.PACKAGE}-{version}.tar.gz"
        root = f"{check_dist.PACKAGE}-{version}"
        with tarfile.open(path, "w:gz") as archive:
            for name in check_dist.SDIST_FILES:
                if name in omit:
                    continue
                info = tarfile.TarInfo(f"{root}/{name}")
                archive.addfile(info)
        return path


class StaticFilesTest(TestCase):
    def test_the_declared_static_files_exist_in_the_source_tree(self):
        # Derived from the vendored CHECKSUMS, so a file recorded by a sync but
        # never committed would otherwise be caught only by a release that ships
        # a wheel missing it. STATIC_FILES holds the path inside the wheel,
        # which under a src layout is not the path in the repository.
        source_root = check_dist.REPO_ROOT / check_dist.SRC_DIR
        for name in check_dist.STATIC_FILES:
            with self.subTest(name=name):
                self.assertTrue((source_root / name).is_file(), name)

    def test_the_vendored_assets_are_derived_rather_than_assumed(self):
        # A CHECKSUMS that parsed to nothing would make every wheel check pass
        # vacuously, which is the one failure mode deriving the list introduces.
        self.assertGreater(len(check_dist.STATIC_FILES), 70)
        self.assertIn(
            f"{check_dist.STATIC_PREFIX}/altcha-workers.js", check_dist.STATIC_FILES
        )
        self.assertIn(
            f"{check_dist.STATIC_PREFIX}/altcha/external/altcha.min.js",
            check_dist.STATIC_FILES,
        )


class CheckWheelTest(DistributionTestCase):
    def test_complete_wheel_passes(self):
        errors = []

        check_dist.check_wheel(self.write_wheel(), errors)

        self.assertEqual([], errors)

    def test_missing_static_file_is_reported(self):
        # altcha-workers.js reaches the wheel through a package-data glob, so a
        # packaging mistake drops it without failing the build or the tests.
        dropped = f"{check_dist.STATIC_PREFIX}/altcha-workers.js"
        errors = []

        check_dist.check_wheel(self.write_wheel(omit=[dropped]), errors)

        self.assertEqual(1, len(errors))
        self.assertIn("altcha-workers.js", errors[0])

    def test_missing_template_is_reported(self):
        template = (
            f"{check_dist.PACKAGE}/templates/{check_dist.PACKAGE}/altcha_widget.html"
        )
        wheel = self.write_wheel(omit=[template])
        errors = []

        check_dist.check_wheel(wheel, errors)

        self.assertEqual([f"Wheel {wheel.name} is missing {template}"], errors)

    def test_missing_licence_is_reported(self):
        # ALTCHA's own licence, which MIT requires to travel with the vendored
        # assets. It reaches the metadata through a license-files entry rather
        # than the package-data glob that puts a second copy under static/, so
        # dropping it leaves the wheel otherwise complete.
        licences = f"{check_dist.PACKAGE}-{VERSION}.dist-info/licenses"
        vendored = f"{licences}/{check_dist.SRC_DIR}/{check_dist.STATIC_PREFIX}"
        errors = []

        check_dist.check_wheel(
            self.write_wheel(omit=[f"{vendored}/altcha/LICENSE.txt"]), errors
        )

        self.assertEqual(1, len(errors))
        self.assertIn("altcha/LICENSE.txt", errors[0])


class CheckSdistTest(DistributionTestCase):
    def test_complete_sdist_passes(self):
        errors = []

        check_dist.check_sdist(self.write_sdist(), errors)

        self.assertEqual([], errors)

    def test_missing_tests_are_reported(self):
        # The sdist is what a distribution packager builds from, so it has to
        # carry the suite and not only the importable package.
        errors = []

        check_dist.check_sdist(self.write_sdist(omit=["tests/test_widget.py"]), errors)

        self.assertEqual(1, len(errors))
        self.assertIn("tests/test_widget.py", errors[0])

    def test_the_files_that_make_the_suite_runnable_are_reported(self):
        """
        Carrying the test modules is not the same as carrying a runnable suite.

        setuptools sweeps `tests/test*.py` into the sdist through a distutils
        default, so those arrive whatever MANIFEST.in says — and the sdist used
        to ship exactly that and nothing else, with collection ending at
        `from .test_cache import ...`. These reach it only through MANIFEST.in,
        which is what makes asserting on them worth anything.
        """
        for name in ("tests/__init__.py", "tests/settings.py", "tests/urls.py"):
            with self.subTest(name=name):
                errors = []

                check_dist.check_sdist(self.write_sdist(omit=[name]), errors)

                self.assertEqual(1, len(errors))
                self.assertIn(name, errors[0])


class SoleDistributionTest(DistributionTestCase):
    def test_one_wheel(self):
        wheel = self.write_wheel()
        errors = []

        self.assertEqual(
            wheel, check_dist.sole_distribution(self.dist_dir, ".whl", errors)
        )
        self.assertEqual([], errors)

    def test_no_wheel_is_reported(self):
        errors = []

        self.assertIsNone(check_dist.sole_distribution(self.dist_dir, ".whl", errors))
        self.assertEqual(1, len(errors))

    def test_leftover_wheel_is_reported(self):
        # Publishing uploads everything in dist/, so a stale build would be
        # pushed to PyPI alongside the intended one.
        self.write_wheel()
        self.write_wheel(version="0.9.0")
        errors = []

        self.assertIsNone(check_dist.sole_distribution(self.dist_dir, ".whl", errors))
        self.assertEqual(1, len(errors))
        self.assertIn("0.9.0", errors[0])


class VersionTest(DistributionTestCase):
    def test_distribution_version_from_filenames(self):
        self.assertEqual(VERSION, check_dist.distribution_version(self.write_wheel()))
        self.assertEqual(VERSION, check_dist.distribution_version(self.write_sdist()))

    def test_agreeing_versions_pass(self):
        errors = []

        check_dist.check_versions(
            self.write_wheel(), self.write_sdist(), f"v{VERSION}", errors
        )

        self.assertEqual([], errors)

    def test_tag_ahead_of_the_version_bump_is_reported(self):
        errors = []

        check_dist.check_versions(
            self.write_wheel(), self.write_sdist(), "v1.0.1", errors
        )

        self.assertEqual(1, len(errors))
        self.assertIn("tag v1.0.1 says 1.0.1", errors[0])

    def test_version_bumped_in_only_one_place_is_reported(self):
        # pyproject.toml drives the filenames; __init__.py is bumped by hand.
        errors = []

        check_dist.check_versions(
            self.write_wheel(init_version="0.9.0"), self.write_sdist(), None, errors
        )

        self.assertEqual(1, len(errors))
        self.assertIn("__version__ says 0.9.0", errors[0])

    def test_missing_dunder_version_is_reported(self):
        wheel = self.write_wheel(omit=[f"{check_dist.PACKAGE}/__init__.py"])
        errors = []

        check_dist.check_versions(wheel, None, None, errors)

        self.assertEqual(1, len(errors))
        self.assertIn("No __version__", errors[0])

    def test_no_expected_version_still_compares_what_is_there(self):
        errors = []

        check_dist.check_versions(
            self.write_wheel(), self.write_sdist(version="0.9.0"), None, errors
        )

        self.assertEqual(1, len(errors))


class SyncAltchaTest(TestCase):
    """
    The vendoring script owns the whole ``altcha/`` tree.

    A file it did not write is a leftover from an earlier version. Recorded in
    CHECKSUMS it would pass ``just check-altcha`` as though upstream had
    published it, and `check_dist` derives the wheel's required assets from that
    same file — so it would ship, having been verified against nothing.
    """

    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo_root = Path(temporary.name)
        self.root = self.repo_root / "altcha"
        self.root.mkdir()

        for name, value in {
            "REPO_ROOT": self.repo_root,
            "STATIC_ROOT": self.root,
            "CHECKSUMS": self.root / "CHECKSUMS",
            "VENDOR_JSON": self.root / "VENDOR.json",
        }.items():
            patcher = mock.patch.object(sync_altcha, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def downloaded(self, *translations):
        """What a sync holds after downloading, the path standing in for bytes."""
        assets = {path: path.encode() for path in sync_altcha.ASSETS}
        assets.update({f"dist/i18n/{name}": name.encode() for name in translations})
        return assets

    def sync(self, assets):
        written = sync_altcha.write_assets(assets)
        sync_altcha.write_checksums("9.9.9", written)
        return written

    def recorded(self):
        return set(sync_altcha.read_checksums())

    def test_a_renamed_asset_does_not_survive_the_sync(self):
        """
        The case the i18n-only pruning missed.

        Upstream renames a worker, ASSETS is updated to match, and the file
        under the old name is left with nothing to overwrite it.
        """
        stale = self.root / "workers" / "sha-old-name.js"
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b"from an earlier altcha")

        self.sync(self.downloaded("fr-fr.js"))

        self.assertFalse(stale.exists())
        self.assertNotIn("workers/sha-old-name.js", self.recorded())

    def test_a_dropped_translation_is_removed(self):
        dropped = self.root / "i18n" / "xx-xx.js"
        dropped.parent.mkdir(parents=True)
        dropped.write_bytes(b"a language upstream stopped shipping")

        self.sync(self.downloaded("fr-fr.js"))

        self.assertFalse(dropped.exists())
        self.assertNotIn("i18n/xx-xx.js", self.recorded())
        self.assertIn("i18n/fr-fr.js", self.recorded())

    def test_checksums_record_exactly_what_was_written(self):
        written = self.sync(self.downloaded("fr-fr.js", "de.js"))

        self.assertEqual(written, self.recorded())
        self.assertIn("i18n/de.js", written)
        self.assertIn("external/altcha.min.js", written)

    def test_the_synced_tree_passes_its_own_check(self):
        self.sync(self.downloaded("fr-fr.js"))

        self.assertEqual([], sync_altcha.check_checksums())

    def test_a_file_dropped_in_by_hand_is_not_blessed(self):
        # Nothing under altcha/ is ours, so an unrecognised file is never
        # something to record — whatever put it there.
        intruder = self.root / "extra.js"
        intruder.write_bytes(b"not from upstream")

        self.sync(self.downloaded("fr-fr.js"))

        self.assertFalse(intruder.exists())
        self.assertNotIn("extra.js", self.recorded())

    def test_an_emptied_directory_is_removed(self):
        orphan = self.root / "legacy" / "nested"
        orphan.mkdir(parents=True)
        (orphan / "old.js").write_bytes(b"from a layout upstream abandoned")

        self.sync(self.downloaded("fr-fr.js"))

        self.assertFalse((self.root / "legacy").exists())

    def test_the_provenance_files_are_kept(self):
        # They live in the tree but are not vendored assets: deleting VENDOR.json
        # as a stray would throw away the provenance the sync just recorded.
        vendor = self.root / "VENDOR.json"
        vendor.write_text('{"version": "9.9.9"}')

        self.sync(self.downloaded("fr-fr.js"))

        self.assertTrue(vendor.exists())
        self.assertTrue((self.root / "CHECKSUMS").exists())
        self.assertNotIn("CHECKSUMS", self.recorded())
        self.assertNotIn("VENDOR.json", self.recorded())


class ReleaseNotesTest(TestCase):
    def notes(self, version, changelog=CHANGELOG):
        body = release_notes.section_body(changelog.splitlines(), version)
        return "\n".join(body).strip()

    def test_extracts_the_requested_section_only(self):
        notes = self.notes("v1.0.0")

        self.assertIn("Intro paragraph", notes)
        self.assertNotIn("Later release", notes)
        self.assertNotIn("Never released", notes)

    def test_the_preamble_belongs_to_no_section(self):
        # It sits above the first `##`, so nothing bounds it into a release.
        # Publishing it would repeat the Keep a Changelog boilerplate on every
        # GitHub release.
        for version in ("v2.0.0", "v1.0.0"):
            with self.subTest(version=version):
                self.assertNotIn("Preamble text", self.notes(version))

    def test_subsections_survive(self):
        # `###` is below the section level, so it is body text rather than a
        # boundary, and reaches the release page as a heading.
        self.assertIn("### Features", self.notes("v1.0.0"))

    def test_the_section_is_published_verbatim(self):
        # The changelog is already Markdown: links and backtick literals mean on
        # the release page exactly what they mean in the file, so nothing is
        # rewritten on the way out.
        notes = self.notes("v1.0.0")

        self.assertIn(
            "[django-altcha](https://github.com/aboutcode-org/django-altcha)", notes
        )
        self.assertIn("`AltchaField`", notes)

    def test_unknown_version_fails(self):
        with self.assertRaises(SystemExit):
            self.notes("v3.0.0")

    def test_unreleased_section_fails(self):
        # Tagging before dating the section would publish notes headed
        # "unreleased"; the release should stop instead.
        with self.assertRaises(SystemExit) as caught:
            self.notes("v0.9.0")

        self.assertIn("unreleased", str(caught.exception))

    def test_a_version_is_not_matched_by_a_prefix_of_it(self):
        # `## v1.0.0 (…)` must not answer a request for v1.0, which would
        # publish the wrong release's notes rather than failing.
        with self.assertRaises(SystemExit):
            self.notes("v1.0")

    def test_last_section_runs_to_the_end_of_the_file(self):
        changelog = CHANGELOG[: CHANGELOG.index("## v0.9.0 (unreleased)")]

        self.assertIn("### Features", self.notes("v1.0.0", changelog))

    def test_changelog_has_a_section_for_the_current_version(self):
        lines = release_notes.CHANGELOG.read_text().splitlines()
        versions = [
            heading.split()[0]
            for _index, heading in release_notes.section_headings(lines)
        ]

        self.assertIn(f"v{__version__}", versions)
