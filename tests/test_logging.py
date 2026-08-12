from unittest import mock

from django import forms
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase
from django.test import override_settings

from django_altcha_widget import AltchaField
from django_altcha_widget import get_cache

from .test_cache import UnreachableCache
from .test_field import make_valid_payload


class AltchaFieldLoggingTest(TestCase):
    def setUp(self):
        class TestForm(forms.Form):
            altcha_field = AltchaField()

        self.form_class = TestForm
        # The replay cache outlives a test, and nothing declares the order
        # these run in relative to the other files claiming challenges.
        get_cache().clear()

    @mock.patch("altcha.verify_solution")
    def test_invalid_token_logs_warning(self, mock_verify_solution):
        mock_verify_solution.return_value = mock.Mock(
            verified=False, error="bad signature"
        )
        form = self.form_class(data={"altcha_field": "anything"})
        with self.assertLogs("django_altcha_widget", level="WARNING") as captured:
            form.is_valid()
        self.assertEqual(len(captured.records), 1)
        self.assertEqual(captured.records[0].levelname, "WARNING")
        self.assertIn("bad signature", captured.output[0])

    @mock.patch("altcha.verify_solution")
    def test_verification_exception_is_logged_with_traceback(
        self, mock_verify_solution
    ):
        mock_verify_solution.side_effect = RuntimeError("boom")
        form = self.form_class(data={"altcha_field": "anything"})
        with self.assertLogs("django_altcha_widget", level="ERROR") as captured:
            form.is_valid()
        self.assertEqual(captured.records[0].levelname, "ERROR")
        # Confirms traceback is captured
        self.assertIsNotNone(captured.records[0].exc_info)

    @mock.patch("altcha.verify_solution")
    def test_replay_attempt_logs_warning(self, mock_verify_solution):
        mock_verify_solution.return_value = mock.Mock(verified=True)
        valid_payload = make_valid_payload(signature="replay-test-1")
        # First submission succeeds and marks the challenge as used.
        self.form_class(data={"altcha_field": valid_payload}).is_valid()
        # Second submission should log a replay warning.
        form = self.form_class(data={"altcha_field": valid_payload})
        with self.assertLogs("django_altcha_widget", level="WARNING") as captured:
            form.is_valid()
        self.assertTrue(
            any("replay" in r.getMessage().lower() for r in captured.records)
        )

    @mock.patch("altcha.verify_solution")
    def test_unavailable_cache_is_not_logged_as_a_replay(self, mock_verify_solution):
        """
        The log is the whole point of separating the two.

        An operator reading "replay attack detected" on every submission goes
        looking for an attacker; the cache is what actually needs attention.
        """
        mock_verify_solution.return_value = mock.Mock(verified=True)
        payload = make_valid_payload(signature="unreachable-cache-logging")

        with mock.patch(
            "django_altcha_widget.get_cache",
            return_value=UnreachableCache("unreachable", {}),
        ):
            form = self.form_class(data={"altcha_field": payload})
            with self.assertLogs("django_altcha_widget", level="ERROR") as captured:
                form.is_valid()

        self.assertEqual(1, len(captured.records))
        self.assertEqual("ERROR", captured.records[0].levelname)
        self.assertIn("replay protection is unavailable", captured.output[0])
        self.assertNotIn("replay attack detected", captured.output[0])
        # The cause is what an operator needs, so it must reach the traceback.
        self.assertIsNotNone(captured.records[0].exc_info)

    @override_settings(ALTCHA_HMAC_KEY=None)
    def test_missing_hmac_key_logs_error(self):
        from django_altcha_widget import get_hmac_key

        with self.assertLogs("django_altcha_widget", level="ERROR") as captured:
            with self.assertRaises(ImproperlyConfigured):
                get_hmac_key()
        self.assertEqual(captured.records[0].levelname, "ERROR")
