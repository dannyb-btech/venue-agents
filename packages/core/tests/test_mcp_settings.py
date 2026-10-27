"""Connector settings: local defaults never reach a deployed connector."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from va_core.mcp.settings import ConnectorSettings

DEPLOYED = {
    "ENVIRONMENT": "prod",
    "KEY_VAULT_URL": "https://kv.example.vault.azure.net/",
    "MANAGED_IDENTITY_CLIENT_ID": "00000000-0000-0000-0000-000000000001",
    "STAMP_ID": "general-01",
    "RUN_ENVELOPE_KIDS": '["k2026a"]',
    "CONNECTOR_SYSTEM": "gmail",
    "CONNECTOR_ALLOWED_HOSTS": '["va-gmail.internal.example:*"]',
}


def _env(monkeypatch: pytest.MonkeyPatch, **overrides: str | None) -> None:
    for key, value in {**DEPLOYED, **overrides}.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


def test_local_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("CONNECTOR_SYSTEM", "sample")
    settings = ConnectorSettings.load()
    assert settings.run_envelope_kids == ["local"]
    assert settings.connector_allowed_hosts == ["127.0.0.1:*", "localhost:*"]


def test_deployed_settings_load(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    settings = ConnectorSettings.load()
    assert (settings.stamp_id, settings.run_envelope_kids) == ("general-01", ["k2026a"])


@pytest.mark.parametrize(
    ("key", "match"),
    [("STAMP_ID", "stamp_id"), ("RUN_ENVELOPE_KIDS", "run_envelope_kids")],
)
def test_deployed_refuses_local_defaults(
    monkeypatch: pytest.MonkeyPatch, key: str, match: str
) -> None:
    _env(monkeypatch, **{key: None})
    with pytest.raises(ValidationError, match=match):
        ConnectorSettings.load()


def test_deployed_refuses_the_sample_connector(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, CONNECTOR_SYSTEM="sample")
    with pytest.raises(ValidationError, match="sample"):
        ConnectorSettings.load()


@pytest.mark.parametrize("hosts", ['["*"]', '["*:443"]', '[":443"]'])
def test_wildcard_hosts_are_refused(monkeypatch: pytest.MonkeyPatch, hosts: str) -> None:
    _env(monkeypatch, CONNECTOR_ALLOWED_HOSTS=hosts)
    with pytest.raises(ValidationError, match="wildcards"):
        ConnectorSettings.load()
