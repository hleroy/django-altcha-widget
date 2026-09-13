import base64
import datetime
import json
import logging
import time

from django import forms
from django.core.cache import caches
from django.core.exceptions import ImproperlyConfigured
from django.core.serializers.json import DjangoJSONEncoder
from django.forms.widgets import HiddenInput
from django.forms.widgets import Script
from django.http import JsonResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

import altcha

from .conf import get_css_url
from .conf import get_js_url
from .conf import get_setting
from .conf import get_translations_url
from .conf import get_workers_register_url
from .conf import get_workers_urls

__version__ = "1.0.0"

logger = logging.getLogger(__name__)

# Shortest ALTCHA_HMAC_KEY accepted, in characters. 32 hex characters carry the
# 128 bits below which an HMAC key stops being a serious obstacle.
HMAC_KEY_MIN_LENGTH = 32


class ChallengeClaimUnavailable(Exception):
    """
    The replay cache could not be reached, so no claim could be made.

    Distinct from a refused claim: nothing is known about this challenge, as
    opposed to it having been used already.
    """


# Replay entries live in a cache the project uses for everything else, so they
# are namespaced: recognisable when inspecting that cache, and unable to collide
# with a key chosen elsewhere.
CACHE_KEY_PREFIX = "altcha-replay:"

# Options rendered as attributes of the `<altcha-widget>` element.
# https://altcha.org/docs/v2/widget-integration/
#
# `name` is upstream's but is deliberately absent: the template renders it from
# the field's own name, and it is the one attribute a project must not change.
# See `get_altcha_options`.
WIDGET_ATTRIBUTES = (
    "auto",
    "challenge",
    "configuration",
    "display",
    "language",
    "theme",
    "type",
    "workers",
)

# Options collected into the JSON-encoded `configuration` attribute.
# The `fetch` and `verifyFunction` options are intentionally left out as they
# require a JavaScript function value that cannot be expressed from Python.
WIDGET_CONFIGURATION = (
    "audioChallengeLanguage",
    "barPlacement",
    "codeChallenge",
    "codeChallengeDisplay",
    "credentials",
    "debug",
    "disableAutoFocus",
    "floatingAnchor",
    "floatingOffset",
    "floatingPersist",
    "floatingPlacement",
    "hideFooter",
    "hideLogo",
    "humanInteractionSignature",
    "minDuration",
    "mockError",
    "overlayContent",
    "popoverPlacement",
    "retryOnOutOfMemoryError",
    "serverVerificationFields",
    "serverVerificationTimeZone",
    "setCookie",
    "test",
    "timeout",
    "validationMessage",
    "verifyUrl",
)


def get_hmac_key():
    """Return the HMAC key, raising if it is missing or too short."""
    hmac_key = get_setting("ALTCHA_HMAC_KEY")
    if not hmac_key:
        logger.error("ALTCHA_HMAC_KEY setting is not configured")
        raise ImproperlyConfigured("The ALTCHA_HMAC_KEY setting must be provided.")

    if len(hmac_key) < HMAC_KEY_MIN_LENGTH:
        logger.error("ALTCHA_HMAC_KEY setting is too short")
        raise ImproperlyConfigured(
            f"The ALTCHA_HMAC_KEY setting must be at least "
            f"{HMAC_KEY_MIN_LENGTH} characters long. Generate one with: "
            f'python -c "import secrets; print(secrets.token_hex(64))"'
        )
    return hmac_key


def get_challenge_expire_seconds():
    return get_setting("ALTCHA_CHALLENGE_EXPIRE") // 1000


def get_cache():
    """Return the cache backend used for replay attack protection."""
    return caches[get_setting("ALTCHA_CACHE_ALIAS")]


def get_cache_key(challenge):
    """Return the cache key recording that ``challenge`` has been claimed."""
    return f"{CACHE_KEY_PREFIX}{challenge}"


def _is_challenge_used(challenge):
    """
    Whether ``challenge`` has already been claimed. For inspection only.

    Private, and deliberately so: this is a bare read, and a read is exactly
    what replay protection must not be built on. Checking here and claiming
    afterwards reopens the window ``claim_challenge`` exists to close. Use it to
    look at the cache, never to decide whether a payload may be accepted.
    """
    return get_cache().get(key=get_cache_key(challenge)) is not None


def claim_challenge(challenge, timeout):
    """
    Claim a challenge, returning False if it had already been claimed.

    ``add`` stores the key only when it is absent, and does so atomically in
    every cache backend Django ships. A ``get`` followed by a ``set`` cannot
    make that guarantee: between the two, a concurrent worker reads an unused
    challenge and accepts the same payload. Deployments run several worker
    processes against a shared cache, so the two calls are separated by a
    network round trip and the whole point of the check is lost.

    Raises ``ChallengeClaimUnavailable`` when the cache cannot answer at all.
    Django's contract is that ``add`` returns True or False, so a backend that
    neither stores nor refuses is a broken one: it either raised, or it returned
    ``None`` because it was configured to swallow its own errors, as django-redis
    does under ``IGNORE_EXCEPTIONS``. Both have to stay distinguishable from a
    genuine False — read as a refusal, an outage accuses every visitor of
    replaying a challenge for as long as it lasts.
    """
    try:
        claimed = get_cache().add(
            key=get_cache_key(challenge), value=True, timeout=timeout
        )
    except Exception as exc:
        raise ChallengeClaimUnavailable(
            "The replay protection cache raised while claiming a challenge."
        ) from exc

    if claimed is None:
        raise ChallengeClaimUnavailable(
            "The replay protection cache returned no answer while claiming a "
            "challenge, which usually means it is configured to suppress its "
            "own errors."
        )
    return claimed


def get_altcha_challenge(algorithm=None, cost=None, expires=None):
    """
    Generate and return an ``altcha.Challenge``.

    Each argument falls back to its corresponding setting when omitted;
    ``expires`` is a duration in milliseconds, not an instant.
    """
    expires = expires or get_setting("ALTCHA_CHALLENGE_EXPIRE")

    return altcha.create_challenge(
        algorithm=algorithm or get_setting("ALTCHA_ALGORITHM"),
        cost=cost if cost is not None else get_setting("ALTCHA_COST"),
        # `timezone.now()` rather than `datetime.now()`: the latter is naive
        # local time, which is ambiguous across a DST fall-back and converts to
        # the wrong instant for the repeated hour.
        expires_at=timezone.now() + datetime.timedelta(milliseconds=expires),
        hmac_secret=get_hmac_key(),
    )


def module_script(url, attributes=None):
    """
    Return a ``forms.Media`` entry loading ``url`` as an ES module.

    ALTCHA is distributed as an ES module, which the plain ``<script src="...">``
    that ``Media`` renders by default cannot load.
    """
    return Script(url, type="module", **(attributes or {}))


class AltchaWidget(HiddenInput):
    # `HiddenInput` for `input_type = "hidden"`, which is what makes `is_hidden`
    # true and keeps the field out of `visible_fields()`, so `{{ form }}` renders
    # it without a label or a row of its own. Only that class attribute is
    # wanted: `template_name` below replaces the markup, because the control
    # under this field's name is the one `<altcha-widget>` creates at runtime.
    template_name = "django_altcha_widget/altcha_widget.html"

    def __init__(self, options=None, *args, **kwargs):
        self.options = options or {}
        super().__init__(*args, **kwargs)

    @property
    def media(self):
        """
        Return the assets of the widget, for projects relying on ``form.media``
        rather than on the assets included by the widget template.
        """
        # Built from the context the template renders from, so the two entry
        # points cannot disagree about which assets a configuration calls for.
        context = self.get_assets_context()

        js = [module_script(context["js_altcha_url"])]
        if context["js_translations_url"]:
            js.append(module_script(context["js_translations_url"]))

        # The modular build registers no algorithm on its own, the workers have
        # to be declared explicitly. This must happen after the widget module is
        # evaluated, hence the entry being appended last.
        js.append(
            module_script(context["js_workers_register_url"], context["workers_attrs"])
        )
        return forms.Media(css={"all": [context["css_altcha_url"]]}, js=js)

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context.update(self.get_assets_context())
        context["widget"]["altcha_options"] = self.get_altcha_options()
        return context

    @staticmethod
    def get_assets_context():
        """
        Return the asset context for the template.

        The vendored modular build is the only thing served, so there is nothing
        to branch on: every asset below is rendered on every form.
        """
        return {
            "js_altcha_url": get_js_url(),
            "js_translations_url": get_translations_url(),
            "css_altcha_url": get_css_url(),
            "js_workers_register_url": get_workers_register_url(),
            # Resolved server-side and handed to the registration module as a
            # JSON mapping, so they stay correct under a hashed staticfiles
            # storage.
            "workers_attrs": {"data-altcha-workers": json.dumps(get_workers_urls())},
        }

    def get_altcha_options(self):
        """
        Return the ``<altcha-widget>`` attributes for this widget, with the
        options that are not HTML attributes gathered into ``configuration``.
        """
        options = {
            key: value for key, value in self.options.items() if value is not None
        }

        # See `WIDGET_ATTRIBUTES` for why `name` is not an option. `AltchaField`
        # already rejects it; this covers a directly-constructed widget.
        options.pop("name", None)

        # Minted here rather than in `__init__` because a challenge is
        # single-use: one per rendering, not one per widget instance. A
        # `challenge` option holds a URL to fetch from instead.
        if not options.get("challenge"):
            options["challenge"] = get_altcha_challenge().to_dict()

        configuration = options.pop("configuration", None) or {}
        if isinstance(configuration, str):
            configuration = json.loads(configuration)
        configuration = dict(configuration)

        for key in list(options):
            if key not in WIDGET_ATTRIBUTES:
                configuration[key] = options.pop(key)

        if configuration:
            options["configuration"] = configuration

        return self.encode_values(options)

    @staticmethod
    def encode_values(data):
        """Return a shallow copy of `data` where lists and dicts are JSON encoded."""
        encoded = {}
        for key, value in data.items():
            if isinstance(value, (list, dict)):
                # `DjangoJSONEncoder` rather than the plain one: options such as
                # `validationMessage` are the natural place for a translated
                # string, and `gettext_lazy` returns a proxy the stdlib encoder
                # refuses. Rendering a form is not where a project should
                # discover that, so resolve the proxy instead of raising.
                value = json.dumps(value, cls=DjangoJSONEncoder)
            encoded[key] = value
        return encoded


class AltchaField(forms.Field):
    widget = AltchaWidget
    default_error_messages = {
        "error": _("Failed to process CAPTCHA token"),
        "invalid": _("Invalid CAPTCHA token."),
        "required": _("ALTCHA CAPTCHA token is missing."),
        "replay": _("Challenge has already been used."),
    }
    # Options supported by the ALTCHA v3 widget, all optional.
    # The HTML attributes are documented at:
    # https://altcha.org/docs/v2/widget-integration/#html-attributes
    # Every other option is passed through the `configuration` attribute:
    # https://altcha.org/docs/v2/widget-integration/#configuration
    default_options = dict.fromkeys(WIDGET_ATTRIBUTES + WIDGET_CONFIGURATION)

    def __init__(self, *args, **kwargs):
        # An optional proof of work is not a weaker CAPTCHA, it is no CAPTCHA:
        # whatever submits the form without the field passes unchallenged, while
        # the widget still renders and says otherwise. Refused rather than
        # quietly forced back to True, so asking for it is a traceback instead
        # of a protection that silently is not there. A form that needs no
        # CAPTCHA should leave the field out; `ALTCHA_VERIFICATION_ENABLED` is
        # the deliberate way to stop verifying everywhere.
        if "required" in kwargs and not kwargs["required"]:
            raise ValueError(
                "AltchaField cannot be optional: a CAPTCHA that may be left "
                "unsolved is not enforced at all. Omit the field from the forms "
                "that do not need one, or set ALTCHA_VERIFICATION_ENABLED = False "
                "to stop verifying."
            )

        # The field builds the widget itself and hands it the options collected
        # below, so one passed here could only be thrown away — an argument
        # accepted, ignored, and never mentioned again, which is exactly what
        # refusing `required=False` above exists to avoid. `TypeError` rather
        # than that `ValueError`: this is an argument `AltchaField` does not take
        # at all, not a value it will not accept, and it is how a `name` option
        # and every unknown option already fail.
        if "widget" in kwargs:
            raise TypeError(
                "AltchaField does not accept a `widget` argument: it builds its "
                "own widget from the options passed to the field. To render with "
                "a different one, subclass AltchaField and point its `widget` "
                "class attribute at an AltchaWidget subclass."
            )

        widget_options = {
            key: kwargs.pop(key, self.default_options[key])
            for key in self.default_options
        }
        kwargs["widget"] = self.widget(options=widget_options)
        super().__init__(*args, **kwargs)

    def validate(self, value):
        if not get_setting("ALTCHA_VERIFICATION_ENABLED"):
            logger.debug(
                "ALTCHA validation skipped: ALTCHA_VERIFICATION_ENABLED is False"
            )
            return

        # `__init__` refuses `required=False`, so this rejects every empty value
        # and no second emptiness check is needed below.
        super().validate(value)

        # Looked up outside the guard below: a missing or too-short key is the
        # deployment's problem, and turning it into a validation error would
        # hide it behind what looks like every visitor failing the CAPTCHA.
        hmac_key = get_hmac_key()

        try:
            result = altcha.verify_solution(payload=value, hmac_secret=hmac_key)
        except Exception:
            logger.exception("ALTCHA validation raised an unexpected exception")
            raise forms.ValidationError(self.error_messages["error"], code="error")

        if not result.verified:
            logger.warning("ALTCHA validation failed: %s", get_failure_reason(result))
            raise forms.ValidationError(self.error_messages["invalid"], code="invalid")

        self.replay_attack_protection(payload=value)

    def replay_attack_protection(self, payload):
        try:
            challenge, timeout = get_challenge_claim(payload)
        except Exception:
            logger.exception(
                "ALTCHA payload could not be decoded for replay protection"
            )
            raise forms.ValidationError(self.error_messages["error"], code="error")

        try:
            claimed = claim_challenge(challenge, timeout=timeout)
        except ChallengeClaimUnavailable:
            # Fail closed: with no working cache the claim cannot be made, and
            # accepting the payload would drop replay protection for the whole
            # outage. Reported as an error rather than as a replay because the
            # visitor did nothing wrong and neither did their token — an
            # operator reading "replay attack detected" across every submission
            # goes looking for an attacker instead of for the cache.
            logger.exception("ALTCHA replay protection is unavailable")
            raise forms.ValidationError(self.error_messages["error"], code="error")

        if not claimed:
            logger.warning("ALTCHA replay attack detected: challenge already used")
            raise forms.ValidationError(self.error_messages["replay"], code="invalid")


def get_challenge_claim(payload):
    """
    Return the replay cache key for ``payload``, and how long to hold it.

    The signature of a challenge is an HMAC over its parameters, which include a
    random nonce and salt, and is therefore unique to a single challenge.

    The entry has to outlive the challenge it guards. A payload keeps verifying
    until the ``expiresAt`` signed into it, so a timeout taken from
    ``ALTCHA_CHALLENGE_EXPIRE`` opens a replay window whenever the two disagree
    — a challenge minted through ``AltchaChallengeView.expires``, or the setting
    lowered after the challenge was issued. ``expiresAt`` is covered by the
    signature verified just before this runs, so it cannot be inflated.
    """
    payload_data = json.loads(base64.b64decode(payload).decode())
    challenge = payload_data["challenge"]
    signature = challenge["signature"]
    if not signature:
        raise ValueError("Missing challenge signature")
    return signature, get_claim_timeout(challenge["parameters"].get("expiresAt"))


def get_claim_timeout(expires_at):
    """
    Return the seconds a replay entry must be held for a challenge expiring at
    the ``expires_at`` Unix timestamp.

    A challenge carrying no expiry is not one this package mints, but it would
    verify forever; the configured lifetime is the best guess available without
    keeping the entry indefinitely.

    Both branches are floored at a second. A timeout of 0 means "expire
    immediately" to Django's cache API, so the claim would be written and
    dropped before anything could read it — replay protection silently off,
    since the next copy of the payload is granted the claim in turn. The expiry
    branch reaches 0 for an already-expired challenge, and the fallback reaches
    it for any ``ALTCHA_CHALLENGE_EXPIRE`` under a second, which floors to 0.
    """
    if not expires_at:
        return max(get_challenge_expire_seconds(), 1)
    return max(expires_at - int(time.time()), 1)


def get_failure_reason(result):
    """Return a human readable reason for a failed ``VerifySolutionResult``."""
    if result.error:
        return result.error
    if result.expired:
        return "challenge expired"
    if result.invalid_signature:
        return "invalid challenge signature"
    if result.invalid_solution:
        return "invalid solution"
    return "unknown error"


class AltchaChallengeView(View):
    algorithm = None
    cost = None
    expires = None

    # Every challenge is single-use, so one served twice is one wasted: a CDN,
    # a proxy or the browser cache handing the same challenge to several
    # visitors means the first submission burns it and the rest are rejected as
    # replays. `never_cache` keeps the response out of every one of them.
    @method_decorator(never_cache)
    @method_decorator(require_GET)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs):
        # Difficulty comes from the class attributes only — set them with
        # `AltchaChallengeView.as_view(cost=...)`. Reading them from `kwargs`
        # would also read whatever the URLconf captures from the path, letting
        # a caller who requested `.../challenge/1/` pick a cost of 1 and solve
        # the proof of work for nothing.
        challenge = get_altcha_challenge(
            algorithm=self.algorithm,
            cost=self.cost,
            expires=self.expires,
        )
        logger.debug("ALTCHA challenge issued")
        return JsonResponse(challenge.to_dict())
