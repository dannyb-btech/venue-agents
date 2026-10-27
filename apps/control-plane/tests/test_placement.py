"""Placement rules (pure)."""

from __future__ import annotations

import pytest

from va_control_plane.errors import PlacementError
from va_control_plane.placement import StampLoad, choose_stamp
from va_db.catalog.models import Ring, StampStatus

AE = "australiaeast"


def _stamp(
    id: str,
    venues: int = 0,
    *,
    capacity: int = 10,
    ring: Ring = Ring.GENERAL,
    status: StampStatus = StampStatus.ACTIVE,
    region: str = AE,
) -> StampLoad:
    return StampLoad(
        id=id, region=region, ring=ring, status=status, capacity=capacity, venues=venues
    )


def test_new_tenant_goes_to_the_least_loaded_stamp_by_fraction() -> None:
    stamps = [_stamp("a", 5, capacity=10), _stamp("b", 6, capacity=20)]
    assert choose_stamp(stamps, region=AE, ring=Ring.GENERAL).id == "b"


def test_ties_break_on_stamp_id() -> None:
    stamps = [_stamp("b"), _stamp("a")]
    assert choose_stamp(stamps, region=AE, ring=Ring.GENERAL).id == "a"


@pytest.mark.parametrize(
    "excluded",
    [
        _stamp("x", 10),
        _stamp("x", ring=Ring.CANARY),
        _stamp("x", status=StampStatus.DRAINING),
        _stamp("x", status=StampStatus.CLOSED),
        _stamp("x", region="westeurope"),
    ],
    ids=["full", "other-ring", "draining", "closed", "other-region"],
)
def test_unsuitable_stamps_are_skipped(excluded: StampLoad) -> None:
    stamps = [excluded, _stamp("ok", 9)]
    assert choose_stamp(stamps, region=AE, ring=Ring.GENERAL).id == "ok"
    with pytest.raises(PlacementError, match="no active general stamp"):
        choose_stamp([excluded], region=AE, ring=Ring.GENERAL)


def test_tenant_stamp_wins_over_a_less_loaded_one() -> None:
    stamps = [_stamp("home", 8), _stamp("empty")]
    chosen = choose_stamp(stamps, region=AE, ring=Ring.GENERAL, tenant_stamp="home")
    assert chosen.id == "home"


def test_tenant_stamp_may_be_draining_or_another_ring() -> None:
    home = _stamp("home", ring=Ring.CANARY, status=StampStatus.DRAINING)
    assert choose_stamp([home], region=AE, ring=Ring.GENERAL, tenant_stamp="home").id == "home"


@pytest.mark.parametrize(
    ("home", "match"),
    [
        (_stamp("home", 10), "full"),
        (_stamp("home", status=StampStatus.CLOSED), "closed"),
        (_stamp("home", region="westeurope"), "westeurope"),
    ],
)
def test_tenant_stamp_that_cannot_take_the_venue_is_an_error(home: StampLoad, match: str) -> None:
    with pytest.raises(PlacementError, match=match):
        choose_stamp([home, _stamp("other")], region=AE, ring=Ring.GENERAL, tenant_stamp="home")


def test_request_for_another_stamp_than_the_tenants_is_refused() -> None:
    with pytest.raises(PlacementError, match="another stamp"):
        choose_stamp(
            [_stamp("home"), _stamp("other")],
            region=AE,
            ring=Ring.GENERAL,
            tenant_stamp="home",
            requested="other",
        )


def test_requested_stamp_overrides_ring_and_load() -> None:
    stamps = [_stamp("canary", 5, ring=Ring.CANARY), _stamp("general")]
    chosen = choose_stamp(stamps, region=AE, ring=Ring.GENERAL, requested="canary")
    assert chosen.id == "canary"


@pytest.mark.parametrize(
    ("stamp", "match"),
    [
        (_stamp("req", 10), "full"),
        (_stamp("req", status=StampStatus.DRAINING), "not taking new tenants"),
        (_stamp("req", region="westeurope"), "westeurope"),
    ],
)
def test_requested_stamp_must_be_able_to_take_it(stamp: StampLoad, match: str) -> None:
    with pytest.raises(PlacementError, match=match):
        choose_stamp([stamp], region=AE, ring=Ring.GENERAL, requested="req")


def test_unknown_stamps_are_errors() -> None:
    with pytest.raises(PlacementError, match="not registered"):
        choose_stamp([], region=AE, ring=Ring.GENERAL, requested="nope")
    with pytest.raises(PlacementError, match="not registered"):
        choose_stamp([], region=AE, ring=Ring.GENERAL, tenant_stamp="nope")
