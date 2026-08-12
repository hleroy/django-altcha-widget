import json
import time

from django.test import RequestFactory
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse

from django_altcha_widget import AltchaChallengeView


class DjangoAltchaViewTest(TestCase):
    def challenge_parameters(self, view):
        """Call `view` and return the parameters of the challenge it issued."""
        response = view(RequestFactory().get("/altcha/challenge/"))
        return json.loads(response.content)["parameters"]

    def test_challenge_view_options_are_set_through_as_view(self):
        """
        `as_view()` is how the challenge is configured, and the only way.

        Every option is covered here rather than `cost` alone: they are read
        from the class attributes, so a change to that lookup would take all
        three with it.
        """
        parameters = self.challenge_parameters(
            AltchaChallengeView.as_view(algorithm="SHA-512", cost=42, expires=60000)
        )

        self.assertEqual("SHA-512", parameters["algorithm"])
        self.assertEqual(42, parameters["cost"])
        self.assertAlmostEqual(time.time() + 60, parameters["expiresAt"], delta=2)

    def test_challenge_view_options_are_set_through_subclassing(self):
        class HardChallengeView(AltchaChallengeView):
            algorithm = "SHA-384"
            cost = 7

        parameters = self.challenge_parameters(HardChallengeView.as_view())

        self.assertEqual("SHA-384", parameters["algorithm"])
        self.assertEqual(7, parameters["cost"])

    def test_challenge_view_returns_200(self):
        response = self.client.get(reverse("altcha_challenge"))
        self.assertEqual(response.status_code, 200)

    def test_challenge_view_returns_json(self):
        response = self.client.get(reverse("altcha_challenge"))
        self.assertEqual(response["Content-Type"], "application/json")

    def test_challenge_response_contains_expected_keys(self):
        response = self.client.get(reverse("altcha_challenge"))
        data = response.json()

        self.assertEqual(["parameters", "signature"], list(data.keys()))

        expected_keys = [
            "algorithm",
            "cost",
            "keyLength",
            "keyPrefix",
            "nonce",
            "salt",
            "expiresAt",
        ]
        parameters = data["parameters"]
        self.assertEqual(expected_keys, list(parameters.keys()))

        self.assertEqual("PBKDF2/SHA-256", parameters["algorithm"])
        # The `cost` view attribute is applied
        self.assertEqual(100, parameters["cost"])

    def test_challenge_view_is_never_cached(self):
        """
        A challenge is single-use, so one served from a cache is one wasted:
        the first visitor to submit it burns it for everyone else who got the
        same copy from a CDN, a proxy, or the browser.
        """
        response = self.client.get(reverse("altcha_challenge"))
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("private", response["Cache-Control"])

    def test_challenge_view_issues_a_fresh_challenge_every_time(self):
        first = self.client.get(reverse("altcha_challenge")).json()
        second = self.client.get(reverse("altcha_challenge")).json()
        self.assertNotEqual(first["signature"], second["signature"])

    def test_challenge_view_ignores_a_cost_captured_from_the_url(self):
        """
        The proof of work is not something the caller gets to choose.

        A URLconf capturing `cost` used to reach `get_altcha_challenge`, so
        requesting a cost of 1 returned a challenge ~5000 times cheaper to
        solve than the configured one.
        """
        response = self.client.get(
            reverse("altcha_challenge_captured_cost", kwargs={"cost": 1})
        )

        self.assertEqual(100, response.json()["parameters"]["cost"])

    def test_challenge_view_algorithm_and_cost_settings(self):
        with override_settings(ALTCHA_ALGORITHM="SHA-512", ALTCHA_COST=42):
            response = self.client.get(reverse("altcha_challenge_defaults"))

        parameters = response.json()["parameters"]
        self.assertEqual("SHA-512", parameters["algorithm"])
        self.assertEqual(42, parameters["cost"])
