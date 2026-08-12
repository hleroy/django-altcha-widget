import os
import time
from datetime import datetime
from datetime import timedelta

from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase
from django.test import override_settings

from django_altcha_widget import HMAC_KEY_MIN_LENGTH
from django_altcha_widget import get_altcha_challenge
from django_altcha_widget import get_hmac_key


class DjangoAltchaUtilsTest(TestCase):
    def test_get_hmac_key(self):
        self.assertEqual("altcha-insecure-hmac-0123456789abcdef", get_hmac_key())

        with override_settings(ALTCHA_HMAC_KEY=None):
            with self.assertRaises(ImproperlyConfigured):
                get_hmac_key()

    def test_get_hmac_key_rejects_a_short_key(self):
        """Forging a challenge is only as hard as guessing this key."""
        with override_settings(ALTCHA_HMAC_KEY="x" * (HMAC_KEY_MIN_LENGTH - 1)):
            with self.assertRaises(ImproperlyConfigured) as caught:
                get_hmac_key()

        self.assertIn(str(HMAC_KEY_MIN_LENGTH), str(caught.exception))
        # The message says how to produce an acceptable one.
        self.assertIn("secrets.token_hex", str(caught.exception))

    def test_get_hmac_key_accepts_a_key_at_the_minimum_length(self):
        with override_settings(ALTCHA_HMAC_KEY="x" * HMAC_KEY_MIN_LENGTH):
            self.assertEqual("x" * HMAC_KEY_MIN_LENGTH, get_hmac_key())

    def test_get_altcha_challenge_algorithm_and_cost(self):
        # Default ALTCHA_ALGORITHM and ALTCHA_COST are applied
        challenge = get_altcha_challenge()
        self.assertEqual("PBKDF2/SHA-256", challenge.parameters.algorithm)
        self.assertEqual(5000, challenge.parameters.cost)

        # Provided arguments are applied
        challenge = get_altcha_challenge(algorithm="SHA-256", cost=50)
        self.assertEqual("SHA-256", challenge.parameters.algorithm)
        self.assertEqual(50, challenge.parameters.cost)

        # Custom settings are applied
        with override_settings(ALTCHA_ALGORITHM="SCRYPT", ALTCHA_COST=1024):
            challenge = get_altcha_challenge()
            self.assertEqual("SCRYPT", challenge.parameters.algorithm)
            self.assertEqual(1024, challenge.parameters.cost)

    def test_get_altcha_challenge_is_signed(self):
        challenge = get_altcha_challenge()
        self.assertEqual(64, len(challenge.signature))
        # Each challenge is unique
        self.assertNotEqual(challenge.signature, get_altcha_challenge().signature)

    def assertExpiresIn(self, challenge, milliseconds):
        """Assert the challenge expires `milliseconds` from now, within a second."""
        expected = datetime.now() + timedelta(milliseconds=milliseconds)
        self.assertAlmostEqual(
            expected.timestamp(), challenge.parameters.expires_at, delta=1
        )

    def test_get_altcha_challenge_expire(self):
        # Default ALTCHA_CHALLENGE_EXPIRE is applied
        self.assertExpiresIn(get_altcha_challenge(), 1200000)

        # Provided `expires` argument is applied
        self.assertExpiresIn(get_altcha_challenge(expires=10000), 10000)

        # Custom ALTCHA_CHALLENGE_EXPIRE value is applied
        with override_settings(ALTCHA_CHALLENGE_EXPIRE=9999):
            self.assertExpiresIn(get_altcha_challenge(), 9999)

    def test_get_altcha_challenge_expiry_is_an_absolute_instant(self):
        """
        Verification compares the expiry against `time.time()`, so it has to be
        a real epoch instant rather than a local wall-clock reading. Run under a
        non-UTC local time, where the two come apart.
        """
        previous_tz = os.environ.get("TZ")
        os.environ["TZ"] = "Pacific/Auckland"
        time.tzset()
        try:
            challenge = get_altcha_challenge(expires=10000)
            self.assertAlmostEqual(
                time.time() + 10, challenge.parameters.expires_at, delta=1
            )
        finally:
            if previous_tz is None:
                del os.environ["TZ"]
            else:
                os.environ["TZ"] = previous_tz
            time.tzset()
