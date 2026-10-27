"""Control plane errors (typed, so the API maps them to status codes)."""

from __future__ import annotations

from va_core.errors import VenueAgentsError


class PlacementError(VenueAgentsError):
    """No stamp can take the venue, or the request conflicts with where the tenant lives."""

    code = "placement"


class OnboardingConflictError(VenueAgentsError):
    """The onboarding job is already running, or not in a state that can be retried."""

    code = "onboarding_conflict"
