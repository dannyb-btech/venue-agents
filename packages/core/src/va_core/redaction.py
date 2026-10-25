"""Redaction for logs, traces and error messages.

Never log secrets, tokens, full email bodies, bank details, TFNs or payroll lines (security rules).
These helpers are a safety net, not a licence to log raw payloads: log IDs and counts, not content.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from itertools import pairwise
from urllib.parse import urlsplit, urlunsplit

REDACTED = "[REDACTED]"

_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]+=*")
_BASIC = re.compile(r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?basic\s+)[A-Za-z0-9+/]+=*")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*")
# key=value / key: value / "key": "value" pairs in URLs, connection strings, JSON and free text.
# Case-insensitive, so camelCase keys (accessToken, clientSecret, apiKey) match too.
_SECRET_PAIR = re.compile(
    r"(?i)(?<![A-Za-z0-9])([\"']?)(password|passwd|pwd|client[_-]?secret|secret"
    r"|(?:access|refresh|id)?[_-]?token|api[_-]?key|subscription[_-]?key|private[_-]?key"
    r"|accountkey|sharedaccesskey|sig|signature)\1(\s*[=:]\s*)"
    r"(\"[^\"]*\"|'[^']*'|[^\s&;,}]+)"
)
_EMAIL = re.compile(r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
_CARD = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")
_TFN = re.compile(r"\b\d{3}[ -]?\d{3}[ -]?\d{2,3}\b")
_BANK_LABELLED = re.compile(
    r"(?i)\b(bsb|acct|a/c|acc(?:ount)?(?:\s*(?:no\.?|number|num|#))?)(\s*[:#]?\s*)(\d[\d -]{4,}\d)"
)
_BSB = re.compile(r"\b\d{3}-\d{3}\b")

# ATO check-digit weights for 9- and 8-digit tax file numbers.
_TFN_WEIGHTS = {
    9: (1, 4, 3, 7, 5, 8, 6, 9, 10),
    8: (10, 7, 8, 4, 6, 3, 5, 1),
}

# Keys are normalised to snake_case words (`X-Api-Key` -> x_api_key, `accessToken` -> access_token).
# Sensitive if any word, or adjacent word pair, matches; content fields are matched whole.
_SENSITIVE_WORDS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "token",
        "authorization",
        "cookie",
        "signature",
        "sas",
        "tfn",
        "bsb",
        "acct",
        "bank",
    }
)
_SENSITIVE_PAIRS = frozenset(
    {
        "api_key",
        "subscription_key",
        "private_key",
        "client_assertion",
        "connection_string",
        "tax_file",
        "account_number",
        "card_number",
        "auth_code",
        "authorization_code",
    }
)
# Email, document and payroll content: never logged, only IDs and counts.
_CONTENT_KEYS = frozenset(
    {
        "body",
        "body_html",
        "body_text",
        "html",
        "content",
        "snippet",
        "text",
        "subject",
        "attachments",
        "payroll_lines",
        "pay_lines",
    }
)
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _digits(s: str) -> str:
    return "".join(ch for ch in s if ch.isdigit())


def is_valid_tfn(candidate: str) -> bool:
    """True if `candidate` passes the ATO TFN checksum (8 or 9 digits)."""
    digits = _digits(candidate)
    weights = _TFN_WEIGHTS.get(len(digits))
    if weights is None:
        return False
    total = sum(int(d) * w for d, w in zip(digits, weights, strict=True))
    return total % 11 == 0


def is_luhn_valid(candidate: str) -> bool:
    """True if `candidate` passes the Luhn check used by payment card numbers."""
    digits = _digits(candidate)
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _sub_if(
    pattern: re.Pattern[str], text: str, check: Callable[[str], bool], replacement: str
) -> str:
    def repl(m: re.Match[str]) -> str:
        return replacement if check(m.group(0)) else m.group(0)

    return pattern.sub(repl, text)


def redact_text(text: str) -> str:
    """Mask secrets and sensitive personal data in free text."""
    text = _BEARER.sub(f"Bearer {REDACTED}", text)
    text = _BASIC.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
    text = _JWT.sub(REDACTED, text)
    text = _SECRET_PAIR.sub(lambda m: f"{m[1]}{m[2]}{m[1]}{m[3]}{REDACTED}", text)
    text = _EMAIL.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", text)
    text = _BANK_LABELLED.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    text = _sub_if(_CARD, text, is_luhn_valid, REDACTED)
    text = _sub_if(_TFN, text, is_valid_tfn, REDACTED)
    return _BSB.sub(REDACTED, text)


def is_sensitive_key(key: object) -> bool:
    """True if a field with this name must never be logged."""
    k = _CAMEL_BOUNDARY.sub("_", str(key)).lower().replace("-", "_")
    if k in _CONTENT_KEYS:
        return True
    words = [w for w in k.split("_") if w]
    if any(w in _SENSITIVE_WORDS for w in words):
        return True
    return any(f"{a}_{b}" in _SENSITIVE_PAIRS for a, b in pairwise(words))


def redact_value(value: object) -> object:
    """Redact strings, recurse into mappings and sequences; other objects are stringified first."""
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return redact_mapping(value)  # pyright: ignore[reportUnknownArgumentType]
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        items: Sequence[object] = value  # pyright: ignore[reportUnknownVariableType]
        return [redact_value(v) for v in items]
    return redact_text(str(value))


def redact_mapping(data: Mapping[str, object]) -> dict[str, object]:
    """Copy of `data` with sensitive keys replaced and all other values redacted."""
    return {
        str(key): REDACTED if is_sensitive_key(key) else redact_value(value)
        for key, value in data.items()
    }


def redact_url(url: str) -> str:
    """Drop the query string, fragment and userinfo from a URL (they often carry credentials)."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    netloc = f"{host}:{parts.port}" if parts.port else host
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


_STANDARD_RECORD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()
) | {"message", "asctime"}


class RedactingFilter(logging.Filter):
    """Redacts the formatted message, exception text, stack info and `extra=` attributes.

    Attach to handlers (not loggers): `extra=` attributes exist only once the record is built, and
    handler filters see records from every child logger.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # a bad format string must not crash the caller
            message = f"[unformattable log message: {record.msg!s}]"
        record.msg = redact_text(message)
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact_text(record.exc_text)
        if record.stack_info:
            record.stack_info = redact_text(record.stack_info)
        for key, value in list(record.__dict__.items()):
            if key in _STANDARD_RECORD_ATTRS:
                continue
            record.__dict__[key] = REDACTED if is_sensitive_key(key) else redact_value(value)
        return True
