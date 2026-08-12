import time
from unittest import mock

from django.core.cache.backends.locmem import LocMemCache
from django.test import SimpleTestCase
from django.test import override_settings

from django_altcha_widget import ChallengeClaimUnavailable
from django_altcha_widget import _is_challenge_used
from django_altcha_widget import claim_challenge
from django_altcha_widget import get_cache
from django_altcha_widget import get_challenge_expire_seconds
from django_altcha_widget import get_claim_timeout
from django_altcha_widget.conf import get_setting


class UnreachableCache(LocMemCache):
    """A cache whose writes fail outright, as an unreachable backend does."""

    def add(self, *args, **kwargs):
        raise ConnectionError("cache is unreachable")


class SilentlyBrokenCache(LocMemCache):
    """
    A cache that swallows its own errors and answers nothing.

    django-redis behaves this way under ``IGNORE_EXCEPTIONS``: ``add`` returns
    None rather than the True or False Django's contract promises.
    """

    def add(self, *args, **kwargs):
        return None


class DjangoAltchaCacheTest(SimpleTestCase):
    def setUp(self):
        # Named per module, and cleared here rather than inside the tests that
        # happen to need it. The replay cache outlives a test: entries claimed
        # here are visible to every later one, in this file and in any other,
        # and nothing declares the order they run in.
        self.challenge = "cache-test-challenge"
        get_cache().clear()

    @override_settings(ALTCHA_CACHE_ALIAS="altcha")
    @mock.patch("django_altcha_widget.caches")
    def test_get_cache_with_alias(self, mock_caches):
        get_cache()
        mock_caches.__getitem__.assert_called_once_with("altcha")

    def test_get_cache_without_alias_uses_default(self):
        self.assertEqual("default", get_setting("ALTCHA_CACHE_ALIAS"))
        cache = get_cache()
        self.assertIsInstance(cache, LocMemCache)

    def test_claim_and_check_challenge_used(self):
        self.assertFalse(_is_challenge_used(self.challenge))
        claim_challenge(self.challenge, timeout=get_challenge_expire_seconds())
        self.assertTrue(_is_challenge_used(self.challenge))

    def test_replay_entries_are_namespaced(self):
        """
        The entries share a cache with the rest of the project, so the signature
        alone is not the key: a bare signature could collide with a key chosen
        elsewhere, and gives nobody inspecting that cache a clue what it is.
        """
        cache = get_cache()
        claim_challenge(self.challenge, timeout=60)

        self.assertIsNone(cache.get(self.challenge))
        self.assertTrue(cache.get(f"altcha-replay:{self.challenge}"))

    def test_claim_challenge_is_granted_once(self):
        """Only the first claim wins; the cache decides, not a prior read."""
        self.assertTrue(claim_challenge(self.challenge, timeout=60))
        self.assertFalse(claim_challenge(self.challenge, timeout=60))

    def test_challenge_expires(self):
        claim_challenge(self.challenge, timeout=60)
        self.assertTrue(_is_challenge_used(self.challenge))

        # The clock is moved rather than slept through: the entry is dropped on
        # read, by comparing the expiry recorded against `time.time()`, so a
        # real wait buys nothing but a second of wall clock and a margin to get
        # wrong on a loaded runner.
        with mock.patch("time.time", return_value=time.time() + 61):
            self.assertFalse(_is_challenge_used(self.challenge))
            # Expired, so claimable again — which is the point of a timeout.
            self.assertTrue(claim_challenge(self.challenge, timeout=60))

    def test_claim_challenge_reports_an_unreachable_cache(self):
        """A backend that raises is not a challenge that was already claimed."""
        with mock.patch(
            "django_altcha_widget.get_cache",
            return_value=UnreachableCache("unreachable", {}),
        ):
            with self.assertRaises(ChallengeClaimUnavailable):
                claim_challenge(self.challenge, timeout=60)

    def test_claim_challenge_reports_a_cache_that_answers_nothing(self):
        """
        None is not False.

        A backend configured to suppress its own errors returns None, and
        reading that as a refusal would turn an outage into a replay accusation
        against every visitor for as long as it lasted.
        """
        with mock.patch(
            "django_altcha_widget.get_cache",
            return_value=SilentlyBrokenCache("silent", {}),
        ):
            with self.assertRaises(ChallengeClaimUnavailable):
                claim_challenge(self.challenge, timeout=60)

    def test_claim_timeout_follows_the_challenge_expiry(self):
        """A claim outlives the challenge it guards, however it was minted."""
        # A challenge issued with a longer expiry than the configured default,
        # as `AltchaChallengeView.expires` allows, must be remembered for as
        # long as it keeps verifying.
        expires_at = int(time.time()) + 3600
        self.assertGreater(
            get_claim_timeout(expires_at), get_challenge_expire_seconds()
        )
        self.assertAlmostEqual(3600, get_claim_timeout(expires_at), delta=2)

        # A shorter-lived challenge needs a correspondingly shorter claim.
        self.assertAlmostEqual(60, get_claim_timeout(int(time.time()) + 60), delta=2)

    def test_claim_timeout_never_expires_immediately(self):
        """A timeout of 0 would tell Django's cache to drop the entry at once."""
        self.assertEqual(1, get_claim_timeout(int(time.time())))
        self.assertEqual(1, get_claim_timeout(int(time.time()) - 3600))

    def test_claim_timeout_without_an_expiry_falls_back_to_the_setting(self):
        self.assertEqual(get_challenge_expire_seconds(), get_claim_timeout(None))

    @override_settings(ALTCHA_CHALLENGE_EXPIRE=500)
    def test_claim_timeout_fallback_is_floored_at_a_second(self):
        """
        A sub-second setting floors to 0, which is not "no timeout".

        Django's cache reads a timeout of 0 as "expire immediately", so the
        claim would be written and dropped before anything could read it — and
        the next copy of the same payload would be granted the claim in turn,
        with replay protection silently off. Asserting against the setting
        alone would not catch it: the checked expression is the bug.
        """
        self.assertEqual(0, get_challenge_expire_seconds())
        self.assertEqual(1, get_claim_timeout(None))

        # The property that actually matters, through the cache itself.
        self.assertTrue(claim_challenge(self.challenge, get_claim_timeout(None)))
        self.assertTrue(_is_challenge_used(self.challenge))
        self.assertFalse(claim_challenge(self.challenge, get_claim_timeout(None)))
