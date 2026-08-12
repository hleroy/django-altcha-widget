"""
Lazy settings access for django-altcha-widget.

Every read is deferred to call time: a value captured at import time would be
whatever the settings held before the project finished configuring them.
"""

from django.conf import settings
from django.templatetags.static import static

_DEFAULTS = {
    # Set to `False` to skip Altcha validation altogether.
    "ALTCHA_VERIFICATION_ENABLED": True,
    # This key is used to HMAC-sign ALTCHA challenges and must be kept secret.
    "ALTCHA_HMAC_KEY": None,
    # Key derivation function used for the Proof-of-Work challenges.
    # Supported: "PBKDF2/SHA-256", "PBKDF2/SHA-384", "PBKDF2/SHA-512",
    # "SHA-256", "SHA-384", "SHA-512", "ARGON2ID", "SCRYPT".
    # https://altcha.org/docs/v2/proof-of-work-captcha/
    "ALTCHA_ALGORITHM": "PBKDF2/SHA-256",
    # Algorithm-specific cost: iterations for PBKDF2 and SHA, time cost for
    # ARGON2ID and SCRYPT. 5000 is the value recommended upstream for
    # PBKDF2/SHA-256.
    "ALTCHA_COST": 5000,
    # Altcha translations to load, as a language code ("fr-fr") or "all" for the
    # combined bundle. `None` loads none, leaving the widget in English.
    # https://altcha.org/docs/v2/widget-integration/#internationalization-i18n
    "ALTCHA_TRANSLATIONS": None,
    # Challenge expiration duration in milliseconds.
    # Default to 20 minutes as per Altcha security recommendations.
    # https://altcha.org/docs/v2/security-recommendations/
    "ALTCHA_CHALLENGE_EXPIRE": 1200000,
    # Django cache alias used to store challenge data for replay attack protection.
    # Defaults to the "default" cache backend.
    # https://docs.djangoproject.com/en/dev/ref/settings/#caches
    "ALTCHA_CACHE_ALIAS": "default",
}

# Where the vendored Altcha assets live inside this package's static directory.
# They are shipped here rather than installed by the project, so these paths are
# constants: there is nothing for a project to point anywhere else.
#
# Only the modular build is vendored, and it is what these paths name. A strict
# Content-Security-Policy is the single supported mode, which makes the default
# `dist/main/` bundle — with its inlined styles and `blob:` workers — a build
# nothing here can reach.
# https://altcha.org/docs/v2/content-security-policy-csp/
ALTCHA_PATH = "django_altcha_widget/altcha/"
ALTCHA_JS_PATH = f"{ALTCHA_PATH}external/altcha.min.js"
ALTCHA_CSS_PATH = f"{ALTCHA_PATH}external/altcha.css"
ALTCHA_WORKERS_PATH = f"{ALTCHA_PATH}workers/"
ALTCHA_I18N_PATH = f"{ALTCHA_PATH}i18n/"

# File names of the Altcha Proof-of-Work worker scripts. The modular build
# registers no algorithm on its own, so each one has to be handed to
# `$altcha.algorithms` by name.
WORKER_FILENAMES = (
    "pbkdf2.js",
    "sha.js",
    "argon2id.js",
    "scrypt.js",
)

# The module registering those workers. Unlike everything above this file is
# part of django-altcha-widget rather than of Altcha, so it sits outside the
# vendored tree that `scripts/sync_altcha.py` owns.
WORKERS_REGISTER_PATH = "django_altcha_widget/altcha-workers.js"


def get_setting(name):
    """Look up a django-altcha-widget setting, falling back to the default."""
    if name not in _DEFAULTS:
        raise ValueError(f"Unknown django-altcha-widget setting: {name}")
    return getattr(settings, name, _DEFAULTS[name])


def get_js_url():
    """Return the URL of the Altcha widget JavaScript module."""
    return static(ALTCHA_JS_PATH)


def get_css_url():
    """Return the URL of the Altcha stylesheet, which the modular build needs."""
    return static(ALTCHA_CSS_PATH)


def get_translations_url():
    """Return the URL of the requested translations, or None for English."""
    language = get_setting("ALTCHA_TRANSLATIONS")
    if not language:
        return None
    # Tolerated so that both "fr-fr" and "fr-fr.js" name the same file.
    language = language.removesuffix(".js")
    return static(f"{ALTCHA_I18N_PATH}{language}.js")


def get_workers_urls():
    """
    Return the URLs of the Proof-of-Work worker scripts, keyed by file name.

    Resolved one file at a time rather than by resolving the directory once,
    which is what keeps them usable under a hashed storage such as
    ``ManifestStaticFilesStorage``, where only individual files have an entry.
    """
    return {name: static(f"{ALTCHA_WORKERS_PATH}{name}") for name in WORKER_FILENAMES}


def get_workers_register_url():
    """Return the URL of the module registering the Proof-of-Work workers."""
    return static(WORKERS_REGISTER_PATH)
