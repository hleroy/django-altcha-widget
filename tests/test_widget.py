import json
import tempfile
from pathlib import Path

from django import forms
from django.http import QueryDict
from django.test import TestCase
from django.test import override_settings
from django.utils.translation import gettext_lazy as _

import django_altcha_widget
from django_altcha_widget import AltchaField
from django_altcha_widget import AltchaWidget
from django_altcha_widget import conf
from django_altcha_widget.conf import WORKER_FILENAMES
from django_altcha_widget.conf import get_workers_urls

HASHED_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"
    },
}

# The vendored assets, as the widget renders them under the STATIC_URL in
# tests/settings.py. Only the modular build is vendored, hence `external/`.
JS_URL = "/static/django_altcha_widget/altcha/external/altcha.min.js"
CSS_URL = "/static/django_altcha_widget/altcha/external/altcha.css"
JS_TRANSLATIONS_URL = "/static/django_altcha_widget/altcha/i18n/all.js"
# Ours rather than upstream's, so it sits outside the vendored `altcha/` tree.
WORKERS_REGISTER_URL = "/static/django_altcha_widget/altcha-workers.js"


def altcha_element(rendered):
    """
    The `<altcha-widget>` tag alone, on one line.

    The template indents one attribute per line, and the asset tags above it
    carry attributes of their own — `type="module"` among them — so asserting
    on an attribute against the whole render is asserting on the wrong thing.
    """
    element = rendered[rendered.index("<altcha-widget") :]
    return " ".join(element.split())


class DjangoAltchaWidgetTest(TestCase):
    def test_widget_initialization_with_default_options(self):
        widget = AltchaWidget()
        self.assertNotIn("challenge", widget.options)
        self.assertNotIn("auto", widget.options)

    def test_widget_initialization_with_custom_options(self):
        options = {
            "auto": "onload",
            "minDuration": 500,
            "debug": True,
        }
        widget = AltchaWidget(options)
        self.assertEqual(widget.options["auto"], "onload")
        self.assertEqual(widget.options["minDuration"], 500)
        self.assertEqual(widget.options["debug"], True)

    def test_widget_generates_challenge_if_not_provided(self):
        widget = AltchaWidget(options={})  # Pass an empty dictionary
        context = widget.get_context(name="test", value=None, attrs={})
        altcha_options = context["widget"]["altcha_options"]
        challenge = json.loads(altcha_options["challenge"])
        parameters = challenge["parameters"]
        self.assertEqual("PBKDF2/SHA-256", parameters["algorithm"])
        self.assertEqual(5000, parameters["cost"])
        self.assertEqual(32, len(parameters["nonce"]))
        self.assertEqual(32, len(parameters["salt"]))
        self.assertIn("expiresAt", parameters)
        self.assertEqual(64, len(challenge["signature"]))

    def test_widget_challenge_url_is_not_json_encoded(self):
        widget = AltchaWidget(options={"challenge": "/altcha/challenge/"})
        context = widget.get_context(name="test", value=None, attrs={})
        self.assertEqual(
            "/altcha/challenge/", context["widget"]["altcha_options"]["challenge"]
        )

    def test_widget_non_attribute_options_moved_to_configuration(self):
        options = {"auto": "onload", "debug": True, "hideFooter": True}
        widget = AltchaWidget(options)
        altcha_options = widget.get_context("name", None, {})["widget"][
            "altcha_options"
        ]
        self.assertEqual("onload", altcha_options["auto"])
        self.assertNotIn("debug", altcha_options)
        self.assertEqual(
            {"debug": True, "hideFooter": True},
            json.loads(altcha_options["configuration"]),
        )

    def test_widget_explicit_configuration_is_merged(self):
        options = {"configuration": {"timeout": 1000}, "debug": True}
        widget = AltchaWidget(options)
        altcha_options = widget.get_context("name", None, {})["widget"][
            "altcha_options"
        ]
        self.assertEqual(
            {"timeout": 1000, "debug": True},
            json.loads(altcha_options["configuration"]),
        )

    def test_widget_configuration_accepts_a_lazy_translated_string(self):
        """
        A localised option must not take the page down with it.

        `validationMessage` is the natural place for a translated string, and
        `gettext_lazy` returns a proxy that `json.dumps` refuses outright — the
        resulting TypeError escapes template rendering, so the whole page 500s
        rather than just the CAPTCHA.
        """
        widget = AltchaWidget({"validationMessage": _("Please solve the CAPTCHA")})
        altcha_options = widget.get_context("name", None, {})["widget"][
            "altcha_options"
        ]
        self.assertEqual(
            {"validationMessage": "Please solve the CAPTCHA"},
            json.loads(altcha_options["configuration"]),
        )

    def test_widget_name_option_does_not_reach_the_element(self):
        """
        The element carries exactly one `name`, and it is the field's.

        Two would be invalid HTML resolved by document order alone; the parser
        keeps the first, so the option was silently ignored. `AltchaField`
        refuses it outright — this covers the widget built directly.
        """
        element = altcha_element(
            AltchaWidget({"name": "something-else"}).render("altcha", None)
        )

        self.assertTrue(element.startswith('<altcha-widget name="altcha"'), element)
        self.assertNotIn("something-else", element)
        self.assertEqual(1, element.count("name="))

    def test_widget_boolean_options_follow_the_html_convention(self):
        """
        Present means true, absent means false — not the string "False".

        A stringified `key="False"` is a non-empty, and so truthy, attribute
        value: the element would read the option as enabled. No ALTCHA
        attribute is boolean today, which is exactly why this is pinned now.
        """
        element = altcha_element(
            AltchaWidget({"auto": True, "type": False}).render("altcha", None)
        )

        # `auto` is present as a bare attribute, `type` is absent entirely.
        self.assertIn(" auto ", element)
        self.assertNotIn("auto=", element)
        self.assertNotIn("type", element)
        self.assertNotIn("False", element)

    def test_widget_rendering_with_complex_options(self):
        options = {"setCookie": {"name": "altcha", "maxAge": 60}}
        widget = AltchaWidget(options)
        rendered_widget_html = widget.render("name", "value")
        expected = (
            'configuration="{&quot;setCookie&quot;: '
            '{&quot;name&quot;: &quot;altcha&quot;, &quot;maxAge&quot;: 60}}"'
        )
        self.assertIn(expected, rendered_widget_html)

    def test_js_translation_included_if_requested(self):
        widget = AltchaWidget()

        with override_settings(ALTCHA_TRANSLATIONS="all"):
            rendered_widget_html = widget.render("name", "value")
            self.assertIn(JS_TRANSLATIONS_URL, rendered_widget_html)

        with override_settings(ALTCHA_TRANSLATIONS=None):
            rendered_widget_html = widget.render("name", "value")
            self.assertNotIn(JS_TRANSLATIONS_URL, rendered_widget_html)

    def test_widget_renders_the_js_url_through_static(self):
        widget = AltchaWidget()
        rendered_html = widget.render("name", "value")
        self.assertIn(JS_URL, rendered_html)

    def test_widget_respects_custom_static_url(self):
        widget = AltchaWidget()
        with override_settings(STATIC_URL="/assets/"):
            rendered_html = widget.render("name", "value")
        self.assertIn(
            "/assets/django_altcha_widget/altcha/external/altcha.min.js", rendered_html
        )
        self.assertNotIn(JS_URL, rendered_html)

    def test_widget_resolves_translations_url_through_static(self):
        widget = AltchaWidget()
        with override_settings(ALTCHA_TRANSLATIONS="all", STATIC_URL="/assets/"):
            rendered_html = widget.render("name", "value")
        self.assertIn("/assets/django_altcha_widget/altcha/i18n/all.js", rendered_html)


class DjangoAltchaWidgetFormControlTest(TestCase):
    """
    Exactly one control carries the field name, and it is ALTCHA's.

    `<altcha-widget>` renders its own `<input type="hidden">` under the name it
    is given, in the light DOM — verified against the shipped bundles, which
    register the element with Svelte's shadow-DOM flag off and use no
    `ElementInternals` form association. Rendering a second control here would
    leave two sharing one name, resolved only by document order.
    """

    def test_the_widget_renders_no_input_of_its_own(self):
        rendered = AltchaWidget().render("altcha", None)
        self.assertNotIn("<input", rendered)
        self.assertIn('<altcha-widget name="altcha"', rendered)

    def test_a_rebound_form_does_not_echo_the_previous_payload(self):
        """
        A submitted payload has been claimed, so re-rendering it is offering a
        value that can only be rejected as a replay.
        """
        rendered = AltchaWidget().render("altcha", "ALREADY-CLAIMED-PAYLOAD")
        self.assertNotIn("ALREADY-CLAIMED-PAYLOAD", rendered)

    def test_the_field_stays_hidden_without_an_input_of_its_own(self):
        """
        `is_hidden` is the whole reason for the `HiddenInput` base and comes
        from the class, not from the markup that was removed: it keeps the field
        out of `visible_fields()` so `{{ form }}` gives it no label or row.
        """

        class TestForm(forms.Form):
            name = forms.CharField()
            altcha = AltchaField()

        form = TestForm()
        self.assertTrue(AltchaWidget().is_hidden)
        self.assertEqual(["altcha"], [field.name for field in form.hidden_fields()])
        self.assertEqual(["name"], [field.name for field in form.visible_fields()])

    def test_the_altcha_payload_is_the_value_that_is_read(self):
        """
        Pin what `value_from_datadict` does with a repeated name.

        Nothing renders a second control today, but a stray one — re-added here,
        or left in a project's own template — must not be able to shadow the
        payload. `QueryDict` returns the last value, so the payload wins only by
        arriving after; assert it rather than leave it to document order.
        """
        data = QueryDict(mutable=True)
        data.appendlist("altcha", "")
        data.appendlist("altcha", "REAL-PAYLOAD")

        self.assertEqual(
            "REAL-PAYLOAD", AltchaWidget().value_from_datadict(data, {}, "altcha")
        )

    def test_a_missing_payload_is_still_a_required_error(self):
        """
        Removing the input removed the empty value it always submitted, so the
        key is now absent rather than blank. Both must reach the same error.
        """

        class TestForm(forms.Form):
            altcha = AltchaField()

        form = TestForm(data={})
        self.assertFalse(form.is_valid())
        self.assertEqual(["ALTCHA CAPTCHA token is missing."], form.errors["altcha"])


class DjangoAltchaWidgetAssetsTest(TestCase):
    """
    The vendored modular build is served unconditionally.

    There is one installation path, so there is nothing to configure and nothing
    to branch on: every form gets the stylesheet, the modular script and the
    worker registration.
    """

    def test_the_modular_build_is_rendered(self):
        rendered = AltchaWidget().render("name", "value")
        self.assertIn(f'<link rel="stylesheet" href="{CSS_URL}">', rendered)
        self.assertIn(f'<script src="{JS_URL}" type="module">', rendered)
        self.assertIn(f'<script src="{WORKERS_REGISTER_URL}" type="module"', rendered)

    def test_the_default_bundle_is_never_rendered(self):
        # `dist/main/` inlines its styles and instantiates workers from a
        # `blob:` URL, which is what a strict CSP forbids. It is not vendored.
        rendered = AltchaWidget().render("name", "value")
        self.assertNotIn("main/altcha.min.js", rendered)

    def test_module_scripts_are_not_async(self):
        # Module scripts must be evaluated in document order so that the workers
        # are registered after the widget module is loaded.
        rendered = AltchaWidget().render("name", "value")
        self.assertNotIn("async", rendered)

    def test_assets_resolve_through_static_url(self):
        with override_settings(STATIC_URL="/assets/"):
            rendered = AltchaWidget().render("name", "value")

        prefix = "/assets/django_altcha_widget"
        self.assertIn(f'href="{prefix}/altcha/external/altcha.css"', rendered)
        self.assertIn(f'src="{prefix}/altcha/external/altcha.min.js"', rendered)
        self.assertIn(f'src="{prefix}/altcha-workers.js"', rendered)

    def test_workers_are_resolved_individually(self):
        # Resolved one worker at a time so that hashed staticfiles storages
        # produce usable URLs; the directory holding them has no manifest entry.
        prefix = "/static/django_altcha_widget/altcha/workers"
        self.assertEqual(
            {
                "pbkdf2.js": f"{prefix}/pbkdf2.js",
                "sha.js": f"{prefix}/sha.js",
                "argon2id.js": f"{prefix}/argon2id.js",
                "scrypt.js": f"{prefix}/scrypt.js",
            },
            get_workers_urls(),
        )

    def test_workers_mapping_is_rendered_as_json(self):
        rendered = AltchaWidget().render("name", "value")

        self.assertIn("data-altcha-workers=", rendered)
        self.assertIn("/static/django_altcha_widget/altcha/workers/pbkdf2.js", rendered)
        self.assertNotIn("data-altcha-workers-url", rendered)

    def test_workers_under_hashed_storage(self):
        """
        Each worker resolves to its hashed name under ManifestStaticFilesStorage.

        Regression: the workers used to be passed to the registration script as
        a directory, which a hashed storage has no manifest entry for.
        """
        prefix = "django_altcha_widget/altcha/workers"
        manifest = {
            "version": "1.1",
            "paths": {
                f"{prefix}/{name}": f"{prefix}/{name[:-3]}.0123456789ab.js"
                for name in WORKER_FILENAMES
            },
        }
        with tempfile.TemporaryDirectory() as static_root:
            Path(static_root, "staticfiles.json").write_text(json.dumps(manifest))
            with override_settings(STATIC_ROOT=static_root, STORAGES=HASHED_STORAGES):
                urls = get_workers_urls()

        self.assertEqual(
            {
                "pbkdf2.js": f"/static/{prefix}/pbkdf2.0123456789ab.js",
                "sha.js": f"/static/{prefix}/sha.0123456789ab.js",
                "argon2id.js": f"/static/{prefix}/argon2id.0123456789ab.js",
                "scrypt.js": f"/static/{prefix}/scrypt.0123456789ab.js",
            },
            urls,
        )

    def test_the_registration_module_sits_outside_the_vendored_tree(self):
        # altcha-workers.js is ours rather than upstream's, so it is not under
        # the `altcha/` directory `scripts/sync_altcha.py` owns and overwrites.
        rendered = AltchaWidget().render("name", "value")
        self.assertIn(f'src="{WORKERS_REGISTER_URL}"', rendered)
        self.assertNotIn("altcha/altcha-workers.js", rendered)


class DjangoAltchaWidgetMediaTest(TestCase):
    def test_media(self):
        media = AltchaWidget().media
        rendered_js = media.render_js()
        self.assertEqual(2, len(rendered_js))
        self.assertEqual(
            f'<script src="{JS_URL}" type="module"></script>', rendered_js[0]
        )
        # Attributes are rendered sorted, so `data-` comes before `type`.
        self.assertTrue(
            rendered_js[1].startswith(
                f'<script src="{WORKERS_REGISTER_URL}" data-altcha-workers='
            ),
            rendered_js[1],
        )
        self.assertTrue(
            rendered_js[1].endswith(' type="module"></script>'), rendered_js[1]
        )
        self.assertEqual(
            [f'<link href="{CSS_URL}" media="all" rel="stylesheet">'],
            list(media.render_css()),
        )

    def test_media_includes_translations_when_requested(self):
        with override_settings(ALTCHA_TRANSLATIONS="all"):
            rendered_js = AltchaWidget().media.render_js()

        # The registration module stays last: it needs the widget module, and
        # `Media` preserves the order the widget declares.
        self.assertEqual(3, len(rendered_js))
        self.assertEqual(
            f'<script src="{JS_TRANSLATIONS_URL}" type="module"></script>',
            rendered_js[1],
        )
        self.assertIn(WORKERS_REGISTER_URL, rendered_js[2])

    def test_media_workers_mapping(self):
        rendered_js = AltchaWidget().media.render_js()
        self.assertIn(
            "/static/django_altcha_widget/altcha/workers/pbkdf2.js", rendered_js[-1]
        )


class DjangoAltchaWidgetTranslationsTest(TestCase):
    def test_a_single_language_can_be_used_instead_of_the_full_bundle(self):
        with override_settings(ALTCHA_TRANSLATIONS="fr-fr"):
            rendered = AltchaWidget().render("name", "value")

        self.assertIn("/static/django_altcha_widget/altcha/i18n/fr-fr.js", rendered)
        self.assertNotIn(JS_TRANSLATIONS_URL, rendered)

    def test_a_js_suffix_is_tolerated(self):
        # "fr-fr" and "fr-fr.js" name the same file; neither should produce
        # `i18n/fr-fr.js.js`.
        with override_settings(ALTCHA_TRANSLATIONS="fr-fr.js"):
            rendered = AltchaWidget().render("name", "value")

        self.assertIn("/static/django_altcha_widget/altcha/i18n/fr-fr.js", rendered)
        self.assertNotIn("fr-fr.js.js", rendered)


class DjangoAltchaWidgetStaticFilesTest(TestCase):
    """
    ALTCHA is vendored, so every asset the widget names is on disk here.

    The URLs above are resolved through staticfiles, which happily invents a URL
    for a file that does not exist; only this notices a vendored asset that went
    missing, or a path in conf.py that no longer matches what was vendored.
    """

    def setUp(self):
        self.static_dir = Path(django_altcha_widget.__file__).parent / "static"

    def test_every_asset_the_widget_serves_is_shipped(self):
        for path in (
            conf.WORKERS_REGISTER_PATH,
            conf.ALTCHA_JS_PATH,
            conf.ALTCHA_CSS_PATH,
            *(f"{conf.ALTCHA_WORKERS_PATH}{name}" for name in conf.WORKER_FILENAMES),
            f"{conf.ALTCHA_I18N_PATH}all.js",
            f"{conf.ALTCHA_I18N_PATH}fr-fr.js",
        ):
            with self.subTest(path=path):
                self.assertTrue((self.static_dir / path).is_file(), path)

    def test_the_upstream_licence_travels_with_the_assets(self):
        # MIT requires it, and pyproject.toml's license-files points here.
        licence = self.static_dir / conf.ALTCHA_PATH / "LICENSE.txt"
        self.assertIn("Daniel Regeci", licence.read_text())

    def test_every_recorded_asset_is_on_disk(self):
        """
        CHECKSUMS is what `just check-altcha` and `scripts/check_dist.py` both
        read, so an asset recorded but not shipped breaks the release check
        rather than the suite. Catch it here instead.
        """
        vendored = self.static_dir / conf.ALTCHA_PATH
        recorded = [
            line.partition("  ")[2]
            for line in (vendored / "CHECKSUMS").read_text().splitlines()
            if line and not line.startswith("#")
        ]

        self.assertGreater(len(recorded), 70)
        missing = [name for name in recorded if not (vendored / name).is_file()]
        self.assertEqual([], missing)

    def test_the_default_bundle_is_not_vendored(self):
        # Nothing can serve it under the strict-CSP-only path, so shipping it
        # would be ~110 KB of dead weight in every wheel.
        self.assertFalse((self.static_dir / conf.ALTCHA_PATH / "main").exists())

    def test_the_regional_bundles_are_not_vendored(self):
        """
        `I18N_EXCLUDED` drops them, and the README says so.

        They are upstream's, so `ALTCHA_TRANSLATIONS = "europe"` resolves
        through `static()` like any other name — to a URL with no file behind
        it. Nothing else notices, which is why the exclusion is pinned here: it
        is what the README's warning describes, and the two have to agree.
        """
        i18n = self.static_dir / conf.ALTCHA_I18N_PATH
        for name in ("africa", "americas", "asia", "europe"):
            with self.subTest(name=name):
                self.assertFalse((i18n / f"{name}.js").exists())

        # Not a blanket absence: the combined bundle is vendored.
        self.assertTrue((i18n / "all.js").is_file())
