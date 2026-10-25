import logging

import pytest

from va_core.redaction import (
    REDACTED,
    RedactingFilter,
    is_luhn_valid,
    is_sensitive_key,
    is_valid_tfn,
    redact_mapping,
    redact_text,
    redact_url,
)

# ATO's published example TFN (valid checksum) and a near miss.
VALID_TFN = "123 456 782"
INVALID_TFN = "123 456 789"


def test_tfn_checksum() -> None:
    assert is_valid_tfn(VALID_TFN)
    assert is_valid_tfn("123456782")
    assert not is_valid_tfn(INVALID_TFN)
    assert not is_valid_tfn("1234")


def test_redacts_valid_tfn_but_keeps_ordinary_numbers() -> None:
    assert redact_text(f"TFN {VALID_TFN} on file") == f"TFN {REDACTED} on file"
    assert redact_text(f"Invoice {INVALID_TFN}") == f"Invoice {INVALID_TFN}"
    assert redact_text("Booking for 120 guests on 2026-11-05") == (
        "Booking for 120 guests on 2026-11-05"
    )


def test_redacts_luhn_valid_card_only() -> None:
    assert is_luhn_valid("4111 1111 1111 1111")
    assert redact_text("card 4111 1111 1111 1111 ok") == f"card {REDACTED} ok"
    assert redact_text("ref 4111 1111 1111 1112") == "ref 4111 1111 1111 1112"


def test_redacts_bank_details() -> None:
    out = redact_text("Pay to BSB 062-000 Acc 12345678 please")
    assert "062-000" not in out
    assert "12345678" not in out
    assert redact_text("account number: 1234 5678 90") == f"account number: {REDACTED}"
    assert redact_text("bsb 062-000") == f"bsb {REDACTED}"


@pytest.mark.parametrize(
    "text",
    [
        "Authorization: Bearer abc.def-123_xyz==",
        # Fake JWT: {"alg":"HS256"}.{"sub":"1"}."signature"
        "token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl",  # gitleaks:allow
        "https://api.example.com/x?api_key=sk_live_abc123&page=2",
        "Server=db;Password=hunter2;User Id=app",
        "AccountKey=Zm9vYmFy==;EndpointSuffix=core.windows.net",
        'client_secret: "s3cr3t value"',
    ],
)
def test_redacts_credentials(text: str) -> None:
    out = redact_text(text)
    for secret in ("abc.def-123", "eyJhbGci", "sk_live_abc123", "hunter2", "Zm9vYmFy", "s3cr3t"):
        assert secret not in out
    assert REDACTED in out


def test_redacts_json_camel_case_and_basic_auth() -> None:
    text = '{"refresh_token": "opaqueRT123", "accessToken":"abc", "password":"p", "rows": 3}'
    out = redact_text(text)
    for secret in ("opaqueRT123", '"abc"', '"p"'):
        assert secret not in out
    assert '"rows": 3' in out
    assert "dXNlcjpwYXNz" not in redact_text("Authorization: Basic dXNlcjpwYXNz")
    assert redact_text("tokens_in=1200 basic plan") == "tokens_in=1200 basic plan"


def test_redacts_acct_label() -> None:
    assert redact_text("acct 12345678") == f"acct {REDACTED}"


@pytest.mark.parametrize(
    "key",
    [
        "X-Api-Key",
        "Ocp-Apim-Subscription-Key",
        "private_key",
        "bsb_number",
        "tfn_number",
        "accessToken",
        "clientSecret",
        "content",
        "subject",
        "auth_code",
    ],
)
def test_sensitive_keys(key: str) -> None:
    assert is_sensitive_key(key)


@pytest.mark.parametrize("key", ["tokens_in", "venue_id", "idempotency_key", "status", "rows"])
def test_ordinary_keys(key: str) -> None:
    assert not is_sensitive_key(key)


def test_masks_email_local_part() -> None:
    assert redact_text("from jane.citizen@example.com.au") == "from j***@example.com.au"


def test_redact_mapping_nested() -> None:
    data: dict[str, object] = {
        "venue": "OR",
        "refresh_token": "abc",
        "Client-Secret": "xyz",
        "body": "Dear chef, full email text",
        "tokens_in": 1200,
        "guest": {"email": "sam@example.com", "notes": [f"TFN {VALID_TFN}", 3]},
    }
    out = redact_mapping(data)
    assert out["venue"] == "OR"
    assert out["refresh_token"] == REDACTED
    assert out["Client-Secret"] == REDACTED
    assert out["body"] == REDACTED
    assert out["tokens_in"] == 1200
    assert out["guest"] == {"email": "s***@example.com", "notes": [f"TFN {REDACTED}", 3]}
    assert data["refresh_token"] == "abc"  # input untouched


def test_redact_url_drops_query_and_userinfo() -> None:
    url = "https://user:pass@api.xero.com:443/api.xro/2.0/Invoices?code=abc#frag"
    assert redact_url(url) == "https://api.xero.com:443/api.xro/2.0/Invoices"
    assert redact_url("/relative/path?token=x") == "/relative/path"


def test_logging_filter_redacts_message_args_and_extras() -> None:
    record = logging.LogRecord(
        "va.test", logging.INFO, __file__, 1, "calling %s with %s", ("svc", "Bearer abc123"), None
    )
    record.__dict__.update(access_token="abc123", detail="email bob@example.com")
    assert RedactingFilter().filter(record)
    assert record.getMessage() == f"calling svc with Bearer {REDACTED}"
    assert record.__dict__["access_token"] == REDACTED
    assert record.__dict__["detail"] == "email b***@example.com"
