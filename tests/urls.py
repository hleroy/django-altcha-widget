from django.urls import path

from django_altcha_widget import AltchaChallengeView

urlpatterns = [
    path(
        "altcha/challenge/",
        AltchaChallengeView.as_view(cost=100),
        name="altcha_challenge",
    ),
    path(
        "altcha/challenge/defaults/",
        AltchaChallengeView.as_view(),
        name="altcha_challenge_defaults",
    ),
    # A URLconf capturing the difficulty from the path. Nothing suggests routing
    # the view this way; it is here to pin down that doing so hands the caller
    # no say over the proof of work.
    path(
        "altcha/challenge/<int:cost>/",
        AltchaChallengeView.as_view(cost=100),
        name="altcha_challenge_captured_cost",
    ),
]
