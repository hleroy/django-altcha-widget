#!/usr/bin/env python
"""
Print the CHANGELOG.md section for a release.

    python scripts/release_notes.py v1.0.0

The GitHub release notes are written rather than generated from the merged pull
request titles, so a reader gets the changelog entry instead of a list of
`chore(deps)` commits. Extraction failing when the version has no dated section
is the point as much as the notes are: it turns "the changelog was not updated"
into a failed release rather than an empty one.

That only holds if it fails *before* the upload, since a version on PyPI can be
yanked but never replaced. The release workflow therefore runs this twice: once
in the build job as a gate, and again after publishing to produce the notes.
The unit tests cannot stand in for the gate — `(unreleased)` is the correct
state for the version under development, so only a tag can tell the two apart.

The changelog is Markdown and GitHub renders release notes as Markdown, so a
section is published exactly as written. What is left here is choosing which
lines to publish, and refusing to publish at all.
"""

import argparse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

# `## v1.0.0 (2026-08-09)`. This level is what separates a release from the `#`
# title, from the `###` subsections within a release, and from the Keep a
# Changelog preamble — which sits above the first heading and so belongs to no
# section, and is never published.
SECTION_HEADING = "## "


def section_headings(lines):
    """(index, text) for every release heading, in file order."""
    return [
        (index, line.removeprefix(SECTION_HEADING).strip())
        for index, line in enumerate(lines)
        if line.startswith(SECTION_HEADING)
    ]


def section_body(lines, version):
    """The lines of the `version` section, without its heading."""
    headings = section_headings(lines)

    for position, (index, heading) in enumerate(headings):
        if heading.split()[:1] != [version]:
            continue
        if "unreleased" in heading.casefold():
            raise SystemExit(
                f"{CHANGELOG.name} still marks {version} as unreleased; "
                "set the release date before tagging"
            )
        end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        return lines[index + 1 : end]

    raise SystemExit(f"No section for {version} in {CHANGELOG.name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "version",
        help="release version, with or without the leading v (e.g. v1.0.0)",
    )
    args = parser.parse_args()

    version = args.version if args.version.startswith("v") else f"v{args.version}"

    if not CHANGELOG.is_file():
        raise SystemExit(f"{CHANGELOG} is missing")

    notes = "\n".join(section_body(CHANGELOG.read_text().splitlines(), version)).strip()
    if not notes:
        raise SystemExit(f"The {version} section of {CHANGELOG.name} is empty")

    print(notes)


if __name__ == "__main__":
    main()
