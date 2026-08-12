import base64
import json
from unittest import mock

from django import forms
from django.core.cache.backends.locmem import LocMemCache
from django.core.exceptions import ImproperlyConfigured
from django.forms import ValidationError
from django.test import TestCase
from django.test import override_settings

import altcha

from django_altcha_widget import AltchaField
from django_altcha_widget import AltchaWidget
from django_altcha_widget import _is_challenge_used
from django_altcha_widget import get_altcha_challenge
from django_altcha_widget import get_cache
from django_altcha_widget import get_challenge_expire_seconds

from .test_cache import SilentlyBrokenCache
from .test_cache import UnreachableCache

# Named per module: the replay cache is shared and outlives a test, so a key
# two files both claim couples them through whatever order they happen to run
# in. This one used to match `test_cache.py`'s.
TEST_CHALLENGE = "field-test-challenge"


class BlindReadCache(LocMemCache):
    """
    A cache that stores normally but whose reads always miss.

    This is what a worker racing another one sees: the challenge has been
    claimed, but not yet by anyone this read can observe. Validation must reject
    the replay on the strength of the write alone.
    """

    def get(self, *args, **kwargs):
        return None


def make_valid_payload(signature=TEST_CHALLENGE):
    """Return a base64-encoded ALTCHA v2 payload skeleton."""
    return encode_payload(
        {
            "challenge": {"parameters": {}, "signature": signature},
            "solution": {"counter": 1, "derivedKey": "00"},
        }
    )


def encode_payload(payload_dict):
    """Return `payload_dict` in the base64-encoded JSON form ALTCHA submits."""
    return base64.b64encode(json.dumps(payload_dict).encode()).decode()


def solved_payload(**kwargs):
    """Mint a real challenge, solve it, and return the submittable payload."""
    challenge = get_altcha_challenge(algorithm="PBKDF2/SHA-256", cost=100, **kwargs)
    return altcha.Payload(challenge, altcha.solve_challenge(challenge)).to_base64()


class DjangoAltchaFieldTest(TestCase):
    def setUp(self):
        class TestForm(forms.Form):
            altcha_field = AltchaField()

        self.form_class = TestForm
        get_cache().clear()

    def test_altcha_field_renders_widget(self):
        form = self.form_class()
        self.assertIsInstance(form.fields["altcha_field"].widget, AltchaWidget)

    def test_altcha_field_options_to_widget(self):
        altcha_field = AltchaField(display="floating", timeout=10000)
        self.assertEqual("floating", altcha_field.widget.options["display"])
        self.assertEqual(10000, altcha_field.widget.options["timeout"])

    def test_altcha_field_unknown_option_is_rejected(self):
        with self.assertRaises(TypeError):
            AltchaField(not_an_altcha_option=50)

    def test_altcha_field_rejects_a_name_option(self):
        """
        `name` is the field's, not an option.

        Accepted, it would emit a second `name` attribute on the element and be
        silently dropped by the parser — and if it ever won instead, ALTCHA
        would submit its hidden input under a name the field never reads.
        """
        with self.assertRaises(TypeError):
            AltchaField(name="something-else")

    def test_altcha_field_rejects_a_caller_supplied_widget(self):
        """
        The field builds its own widget, so one passed in could only be dropped.

        Accepting and then silently replacing it is the same failure refusing
        `required=False` exists to avoid: an argument taken and ignored, with
        the options meant for it going nowhere.
        """
        for widget in (AltchaWidget, AltchaWidget()):
            with self.subTest(widget=widget):
                with self.assertRaises(TypeError):
                    AltchaField(widget=widget)

        with self.assertRaises(TypeError) as caught:
            AltchaField(widget=AltchaWidget())

        # The message names the supported way to swap the widget out.
        self.assertIn("subclass", str(caught.exception))

    def test_altcha_field_subclass_can_replace_the_widget(self):
        """The `widget` class attribute is the supported override."""

        class CustomWidget(AltchaWidget):
            pass

        class CustomField(AltchaField):
            widget = CustomWidget

        field = CustomField(theme="dark")

        self.assertIsInstance(field.widget, CustomWidget)
        # Still built by the field, so it still carries the field's options.
        self.assertEqual("dark", field.widget.options["theme"])

    def test_altcha_field_required_by_default(self):
        form = self.form_class(data={"altcha_field": ""})

        self.assertTrue(form.fields["altcha_field"].required)
        self.assertFalse(form.is_valid())
        self.assertEqual(
            ["ALTCHA CAPTCHA token is missing."], form.errors["altcha_field"]
        )

    def test_altcha_field_cannot_be_optional(self):
        """
        An optional proof of work is not a weaker CAPTCHA, it is none at all.

        Whatever submits the form without the field passes unchallenged, so
        honouring `required=False` would be a silent bypass — and forcing it
        back to True would be a silently ignored argument. Refused outright, at
        construction, so it is a traceback rather than either.
        """
        with self.assertRaises(ValueError) as caught:
            AltchaField(required=False)

        # The message points at the supported way to stop verifying.
        self.assertIn("ALTCHA_VERIFICATION_ENABLED", str(caught.exception))

    def test_altcha_field_rejects_any_falsy_required(self):
        """`required=0` disables a field just as thoroughly as `required=False`."""
        for value in (False, 0, None, ""):
            with self.subTest(required=value):
                with self.assertRaises(ValueError):
                    AltchaField(required=value)

    def test_altcha_field_accepts_an_explicit_required_true(self):
        self.assertTrue(AltchaField(required=True).required)

    def test_altcha_field_validate_verification_enabled_setting(self):
        altcha_field = AltchaField()
        with self.assertRaises(ValidationError):
            altcha_field.validate("a_value")

        with override_settings(ALTCHA_VERIFICATION_ENABLED=False):
            self.assertIsNone(altcha_field.validate("a_value"))

    def test_altcha_field_with_missing_value_raises_required_error(self):
        form = self.form_class(data={})
        self.assertFalse(form.is_valid())
        self.assertIn("altcha_field", form.errors)
        self.assertEqual(
            form.errors["altcha_field"][0], "ALTCHA CAPTCHA token is missing."
        )

    @mock.patch("altcha.verify_solution")
    def test_altcha_field_validation_calls_verify_solution(self, mock_verify_solution):
        self.assertFalse(_is_challenge_used(TEST_CHALLENGE))
        mock_verify_solution.return_value = mock.Mock(verified=True)
        valid_payload = make_valid_payload()
        form = self.form_class(data={"altcha_field": valid_payload})
        self.assertTrue(form.is_valid())
        mock_verify_solution.assert_called_once_with(
            payload=valid_payload,
            hmac_secret=mock.ANY,
        )

        # Replay the validation using the same challenge
        self.assertTrue(_is_challenge_used(TEST_CHALLENGE))
        form = self.form_class(data={"altcha_field": valid_payload})
        self.assertFalse(form.is_valid())
        self.assertEqual(
            form.errors["altcha_field"][0], "Challenge has already been used."
        )

    @mock.patch("altcha.verify_solution")
    def test_altcha_field_validation_fails_on_invalid_token(self, mock_verify_solution):
        mock_verify_solution.return_value = mock.Mock(
            verified=False, error="Invalid altcha payload"
        )
        form = self.form_class(data={"altcha_field": "invalid_token"})
        self.assertFalse(form.is_valid())
        self.assertIn("altcha_field", form.errors)
        self.assertEqual(form.errors["altcha_field"][0], "Invalid CAPTCHA token.")

    @mock.patch("altcha.verify_solution")
    def test_altcha_field_validation_handles_exception(self, mock_verify_solution):
        mock_verify_solution.side_effect = Exception("Verification failed")
        form = self.form_class(data={"altcha_field": "some_token"})
        self.assertFalse(form.is_valid())
        self.assertIn("altcha_field", form.errors)
        self.assertEqual(
            form.errors["altcha_field"][0], "Failed to process CAPTCHA token"
        )

    def test_altcha_field_validation_with_a_solved_challenge(self):
        """Solve a real challenge and validate the resulting payload end to end."""
        payload = solved_payload()

        form = self.form_class(data={"altcha_field": payload})
        self.assertTrue(form.is_valid(), form.errors)

        # The very same payload cannot be submitted twice
        form = self.form_class(data={"altcha_field": payload})
        self.assertFalse(form.is_valid())
        self.assertEqual(
            form.errors["altcha_field"][0], "Challenge has already been used."
        )

    def test_altcha_field_replay_is_rejected_without_reading_the_cache(self):
        """
        A replay must be caught by the claim itself, not by a preceding read.

        Reading and then writing leaves a window in which concurrent
        submissions of one payload all see an unclaimed challenge; with worker
        processes and a shared cache that window is a network round trip wide.
        """
        payload = solved_payload()

        with mock.patch(
            "django_altcha_widget.get_cache", return_value=BlindReadCache("blind", {})
        ):
            form = self.form_class(data={"altcha_field": payload})
            self.assertTrue(form.is_valid(), form.errors)

            form = self.form_class(data={"altcha_field": payload})
            self.assertFalse(form.is_valid())
            self.assertEqual(
                form.errors["altcha_field"][0], "Challenge has already been used."
            )

    @mock.patch("altcha.verify_solution")
    def test_altcha_field_rejects_when_the_cache_is_unreachable(self, mock_verify):
        """
        A cache outage fails closed, and is not dressed up as an attack.

        Accepting the payload would drop replay protection for the length of
        the outage; reporting it as a replay would tell every visitor they had
        reused a challenge and send whoever reads the logs after an attacker
        who does not exist.
        """
        mock_verify.return_value = mock.Mock(verified=True)
        payload = make_valid_payload(signature="unreachable-cache")

        with mock.patch(
            "django_altcha_widget.get_cache",
            return_value=UnreachableCache("unreachable", {}),
        ):
            form = self.form_class(data={"altcha_field": payload})
            self.assertFalse(form.is_valid())

        self.assertEqual(
            form.errors["altcha_field"][0], "Failed to process CAPTCHA token"
        )

    @mock.patch("altcha.verify_solution")
    def test_altcha_field_rejects_when_the_cache_answers_nothing(self, mock_verify):
        """A backend suppressing its own errors takes the same path, not replay."""
        mock_verify.return_value = mock.Mock(verified=True)
        payload = make_valid_payload(signature="silent-cache")

        with mock.patch(
            "django_altcha_widget.get_cache",
            return_value=SilentlyBrokenCache("silent", {}),
        ):
            form = self.form_class(data={"altcha_field": payload})
            self.assertFalse(form.is_valid())

        self.assertEqual(
            form.errors["altcha_field"][0], "Failed to process CAPTCHA token"
        )

    def test_altcha_field_replay_claim_outlives_a_long_lived_challenge(self):
        """A challenge valid for longer than the setting stays claimed as long."""
        with override_settings(ALTCHA_CHALLENGE_EXPIRE=3600000):  # 1 hour
            payload = solved_payload()

        # Validated back under the 20 minute default, as a project lowering the
        # setting — or an `AltchaChallengeView.expires` of its own — produces.
        with mock.patch(
            "django_altcha_widget.claim_challenge", return_value=True
        ) as claim:
            form = self.form_class(data={"altcha_field": payload})
            self.assertTrue(form.is_valid(), form.errors)

        timeout = claim.call_args.kwargs["timeout"]
        self.assertGreater(timeout, get_challenge_expire_seconds())
        self.assertAlmostEqual(3600, timeout, delta=5)

    def test_altcha_field_surfaces_a_misconfigured_hmac_key(self):
        """
        A bad key is a deployment error, not a bad token.

        Reported as a validation error it would look like every visitor
        suddenly failing the CAPTCHA, which is a much harder thing to diagnose
        than the exception naming the setting.
        """
        for key in (None, "too-short"):
            with self.subTest(key=key), override_settings(ALTCHA_HMAC_KEY=key):
                form = self.form_class(data={"altcha_field": "any-token"})
                with self.assertRaises(ImproperlyConfigured):
                    form.is_valid()

    def test_altcha_field_validation_with_a_tampered_challenge(self):
        challenge = get_altcha_challenge(algorithm="PBKDF2/SHA-256", cost=100)
        solution = altcha.solve_challenge(challenge)
        payload_data = altcha.Payload(challenge, solution).to_dict()
        payload_data["challenge"]["parameters"]["cost"] = 1
        payload = encode_payload(payload_data)

        form = self.form_class(data={"altcha_field": payload})
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors["altcha_field"][0], "Invalid CAPTCHA token.")

    def test_altcha_field_validation_with_an_expired_challenge(self):
        with override_settings(ALTCHA_CHALLENGE_EXPIRE=-1000):
            payload = solved_payload()

        form = self.form_class(data={"altcha_field": payload})
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors["altcha_field"][0], "Invalid CAPTCHA token.")

    def test_altcha_field_validation_with_a_test_mode_payload(self):
        # The widget emits this payload when the `test` option is enabled.
        payload = encode_payload({"challenge": None, "solution": None, "test": True})

        form = self.form_class(data={"altcha_field": payload})
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors["altcha_field"][0], "Invalid CAPTCHA token.")
