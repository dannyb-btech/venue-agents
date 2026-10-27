"""Request validation for onboarding."""

from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import ValidationError

from va_control_plane.schemas import OnboardVenueIn

BASE = {"venue_name": "OR", "region": "australiaeast", "plan": "pilot"}
TENANT = "0193a000-0000-7000-8000-00000000000a"


def test_new_tenant_request() -> None:
    body = OnboardVenueIn.model_validate({**BASE, "tenant_name": "  OR Group  "})
    assert body.tenant_name == "OR Group"
    assert body.timezone == "Australia/Sydney"


def test_existing_tenant_request() -> None:
    body = OnboardVenueIn.model_validate({**BASE, "tenant_id": TENANT})
    assert body.tenant_id == UUID(TENANT)


@pytest.mark.parametrize(
    "extra", [{}, {"tenant_name": "x", "tenant_id": TENANT}], ids=["neither", "both"]
)
def test_exactly_one_tenant_field(extra: dict[str, str]) -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        OnboardVenueIn.model_validate({**BASE, **extra})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("stamp_id", "Canary_01"),
        ("timezone", "Mars/Olympus"),
        ("region", "Australia East"),
        ("entitlements", ["Events Manager"]),
        ("venue_name", "   "),
        ("tenant_ref", "x"),
    ],
)
def test_bad_values_are_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        OnboardVenueIn.model_validate({**BASE, "tenant_name": "x", field: value})
