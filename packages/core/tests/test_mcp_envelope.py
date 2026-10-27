"""Run envelopes: only a signed, current envelope for this connector and stamp verifies."""

from __future__ import annotations

import time
from uuid import UUID, uuid4

import jwt
import pytest
from pydantic import SecretStr

from va_core.errors import ConfigurationError, EnvelopeError
from va_core.mcp.envelope import (
    ALGORITHM,
    ISSUER,
    EnvelopeKeys,
    HmacEnvelopeVerifier,
    envelope_key_secret_name,
    issue_envelope,
)
from va_core.mcp.testing import MemorySecretStore, make_keys
from va_core.tenancy import TenantContext

CTX = TenantContext(
    tenant_id=UUID("0193a000-0000-7000-8000-00000000000a"),
    venue_id=UUID("0193a000-0000-7000-8000-0000000000a1"),
    stamp_id="canary-local",
)
RUN = uuid4()
TOOLS = ["sample_get_note", "sample_draft_note"]


def _issue(keys: EnvelopeKeys, **overrides: object) -> str:
    kwargs: dict[str, object] = {
        "system": "sample",
        "run_id": RUN,
        "agent": "events-manager",
        "tools": TOOLS,
    }
    kwargs.update(overrides)
    return issue_envelope(keys, CTX, **kwargs)  # type: ignore[arg-type]


def _verifier(keys: EnvelopeKeys, system: str = "sample", stamp: str = "canary-local"):
    return HmacEnvelopeVerifier(keys, system=system, stamp_id=stamp)


def _raw(
    keys: EnvelopeKeys, claims: dict[str, object], *, kid: str = "test", alg: str = ALGORITHM
) -> str:
    key = keys.key(kid)
    assert key is not None
    return jwt.encode(claims, key.get_secret_value(), algorithm=alg, headers={"kid": kid})


def _claims(**overrides: object) -> dict[str, object]:
    now = int(time.time())
    claims: dict[str, object] = {
        "iss": ISSUER,
        "aud": "va-connector:sample",
        "sub": "events-manager",
        "tid": str(CTX.tenant_id),
        "vid": str(CTX.venue_id),
        "stamp": CTX.stamp_id,
        "run": str(RUN),
        "tools": TOOLS,
        "iat": now,
        "exp": now + 300,
        "jti": "abc",
    }
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


def test_round_trip_gives_the_tenant_context() -> None:
    keys = make_keys()
    envelope = _verifier(keys).verify(_issue(keys))
    assert envelope.tenant_id == CTX.tenant_id
    assert envelope.venue_id == CTX.venue_id
    assert envelope.run_id == RUN
    assert envelope.tools == frozenset(TOOLS)
    ctx = envelope.to_context()
    assert (ctx.tenant_id, ctx.venue_id, ctx.stamp_id) == (
        CTX.tenant_id,
        CTX.venue_id,
        "canary-local",
    )
    assert ctx.user_id == "agent:events-manager"
    assert ctx.roles == frozenset()


@pytest.mark.parametrize("token", [None, "", "not-a-jwt", "a.b.c"])
def test_missing_or_malformed(token: str | None) -> None:
    with pytest.raises(EnvelopeError):
        _verifier(make_keys()).verify(token)


def test_expired() -> None:
    keys = make_keys()
    token = _issue(keys, clock=lambda: time.time() - 1000, ttl=300)
    with pytest.raises(EnvelopeError, match="expired"):
        _verifier(keys).verify(token)


def test_issued_in_the_future() -> None:
    keys = make_keys()
    token = _issue(keys, clock=lambda: time.time() + 600)
    with pytest.raises(EnvelopeError):
        _verifier(keys).verify(token)


def test_wrong_audience_is_another_connector() -> None:
    keys = make_keys()
    with pytest.raises(EnvelopeError):
        _verifier(keys, system="gmail").verify(_issue(keys))


def test_wrong_stamp() -> None:
    keys = make_keys()
    with pytest.raises(EnvelopeError, match="another stamp"):
        _verifier(keys, stamp="general-01").verify(_issue(keys))


def test_wrong_issuer() -> None:
    keys = make_keys()
    with pytest.raises(EnvelopeError):
        _verifier(keys).verify(_raw(keys, _claims(iss="someone-else")))


def test_signed_with_another_key() -> None:
    keys, other = make_keys(), make_keys()
    with pytest.raises(EnvelopeError):
        _verifier(keys).verify(_issue(other))


def test_unknown_kid() -> None:
    keys = make_keys()
    other = make_keys("other")
    with pytest.raises(EnvelopeError, match="unknown key"):
        _verifier(keys).verify(_issue(other))


def test_tampered_tenant_fails_the_signature() -> None:
    keys = make_keys()
    header, _payload, signature = _issue(keys).split(".")
    forged = _raw(make_keys(), _claims(tid=str(uuid4()))).split(".")[1]
    with pytest.raises(EnvelopeError):
        _verifier(keys).verify(".".join([header, forged, signature]))


def test_alg_none_is_refused() -> None:
    keys = make_keys()
    token = jwt.encode(_claims(), key="", algorithm="none", headers={"kid": "test"})
    with pytest.raises(EnvelopeError, match="algorithm"):
        _verifier(keys).verify(token)


def test_other_hmac_algorithm_is_refused() -> None:
    keys = make_keys()
    with pytest.raises(EnvelopeError, match="algorithm"):
        _verifier(keys).verify(_raw(keys, _claims(), alg="HS512"))


def test_lifetime_over_the_maximum() -> None:
    keys = make_keys()
    now = int(time.time())
    with pytest.raises(EnvelopeError, match="lifetime"):
        _verifier(keys).verify(_raw(keys, _claims(iat=now, exp=now + 3600)))


@pytest.mark.parametrize("claim", ["tid", "vid", "stamp", "run", "tools", "jti", "sub", "exp"])
def test_missing_claim(claim: str) -> None:
    keys = make_keys()
    with pytest.raises(EnvelopeError):
        _verifier(keys).verify(_raw(keys, _claims(**{claim: None})))


def test_tools_for_another_connector_are_refused() -> None:
    keys = make_keys()
    with pytest.raises(EnvelopeError, match="tool list"):
        _verifier(keys).verify(_raw(keys, _claims(tools=["gmail_send_message"])))


def test_bad_tenant_id_is_refused() -> None:
    keys = make_keys()
    with pytest.raises(EnvelopeError, match="claims"):
        _verifier(keys).verify(_raw(keys, _claims(tid="not-a-uuid")))


def test_issue_refuses_tools_of_another_system() -> None:
    with pytest.raises(ValueError, match="do not belong"):
        _issue(make_keys(), tools=["gmail_search_threads"])


@pytest.mark.parametrize("ttl", [0, 601])
def test_issue_refuses_bad_ttl(ttl: int) -> None:
    with pytest.raises(ValueError, match="ttl"):
        _issue(make_keys(), ttl=ttl)


def test_rotation_old_kid_still_verifies_new_kid_signs() -> None:
    old = make_keys("k1")
    old_key = old.key("k1")
    assert old_key is not None
    rotated = EnvelopeKeys({"k2": SecretStr("n" * 43), "k1": old_key}, "k2")
    verifier = _verifier(rotated)
    assert verifier.verify(_issue(old)).run_id == RUN
    new_token = _issue(rotated)
    assert jwt.get_unverified_header(new_token)["kid"] == "k2"
    assert verifier.verify(new_token).run_id == RUN


@pytest.mark.parametrize("key", ["short", "x" * 42])
def test_short_keys_are_refused(key: str) -> None:
    with pytest.raises(ConfigurationError, match="shorter"):
        EnvelopeKeys({"k1": SecretStr(key)}, "k1")


async def test_load_reads_named_secrets() -> None:
    store = MemorySecretStore({"run-envelope-key-k1": "a" * 43, "run-envelope-key-k0": "b" * 43})
    keys = await EnvelopeKeys.load(store, ["k1", "k0"])
    assert keys.signing_kid == "k1"
    assert store.reads == ["run-envelope-key-k1", "run-envelope-key-k0"]
    assert envelope_key_secret_name("k1") == "run-envelope-key-k1"
