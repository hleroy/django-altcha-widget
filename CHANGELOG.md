# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## v1.0.0 (2026-09-13)

First version of `django-altcha-widget`, a Django form field and widget for
the ALTCHA proof-of-work CAPTCHA.

The project began as a fork of
[django-altcha](https://github.com/aboutcode-org/django-altcha) and is released
under the same MIT License, but it is published as a separate package and shares
no release history with it. It targets the ALTCHA v2 proof-of-work scheme and
the v3 widget. It serves ALTCHA's modular build, so the widget works under a
strict Content Security Policy (no `unsafe-inline` styles, no `blob:` workers).

Requires Python 3.12 or later and Django 6.0 or later.

This is the first release published on PyPI, cut after the package had run in
production. The public API — the field, the widget options and the `ALTCHA_*`
settings — is now covered by [Semantic Versioning](https://semver.org/spec/v2.0.0.html):
a breaking change to any of it means a new major version.
