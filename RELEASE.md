# Release instructions for `django-altcha-widget`

### Automated release workflow

- Create a new `release-x.x.x` branch
- Update the version in:
  - `pyproject.toml`
  - `src/django_altcha_widget/__init__.py`
  - `CHANGELOG.md` (set date)
- Preview the release notes the workflow will publish — the section heading must
  carry the date, not `(unreleased)`, or the release fails:
  ```
  just release-notes vx.x.x
  ```
- Commit and push this branch
- Create a PR and merge once approved
- Tag and push that tag. This will trigger the `pypi-release.yml` GitHub workflow that
  takes care of building the dist release files and upload those to pypi:
  ```
  VERSION=vx.x.x  # <- Set the new version here
  git tag -a $VERSION -m ""
  git push origin $VERSION
  ```
- Review the GitHub release created by the workflow at
  https://github.com/hleroy/django-altcha-widget/releases

The workflow runs the full test matrix before it builds, checks the built
distributions with `just check-dist`, and only creates the GitHub release once
the PyPI upload has succeeded. A failure in any of those stops the release
rather than publishing a partial one — see *The release pipeline* in
`DEVNOTES.md`.

The version bump above lands in two files and the tag repeats it a third time.
The build checks that all three agree, so tagging before bumping fails the run
instead of uploading the previous version. To check before pushing the tag:

```
just dist
just check-dist --expect-version vx.x.x
```

### Manual build

```
cd django-altcha-widget
just dist
```

The distribution files will be available in the local `dist/` directory.
