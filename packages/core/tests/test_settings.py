from pathlib import Path

import pytest
from pydantic import ValidationError

from va_core import CoreSettings, Environment


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("KEY_VAULT_URL", "MANAGED_IDENTITY_CLIENT_ID", "MODEL_GATEWAY_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_environment_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENVIRONMENT")
    with pytest.raises(ValidationError, match="environment"):
        CoreSettings.load()
    monkeypatch.setenv("ENVIRONMENT", "local")
    assert CoreSettings.load().environment is Environment.LOCAL
    assert CoreSettings.load().environment.is_local


def test_reads_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAMP_ID", "general-01")
    monkeypatch.setenv("MODEL_GATEWAY_KEY", "super-secret")
    settings = CoreSettings.load()
    assert settings.stamp_id == "general-01"
    assert settings.model_gateway_key is not None
    assert settings.model_gateway_key.get_secret_value() == "super-secret"
    assert "super-secret" not in repr(settings)


def test_deployed_environment_requires_key_vault_and_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "prod")
    with pytest.raises(ValidationError, match="key_vault_url"):
        CoreSettings.load()

    monkeypatch.setenv("KEY_VAULT_URL", "https://kv-canary.vault.azure.net/")
    monkeypatch.setenv("MANAGED_IDENTITY_CLIENT_ID", "00000000-0000-0000-0000-000000000000")
    settings = CoreSettings.load()
    assert settings.environment is Environment.PROD
    assert not settings.environment.is_local


def test_database_url_is_secret_and_admin_url_is_not_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://va_app_local:pw-runtime@db/stamp")
    monkeypatch.setenv("DATABASE_ADMIN_URL", "postgresql+psycopg://app:pw-admin@db/stamp")
    settings = CoreSettings.load()
    assert settings.database_url is not None
    assert "pw-runtime" not in repr(settings)
    assert not hasattr(settings, "database_admin_url")


def test_empty_values_in_env_file_mean_unset(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "KEY_VAULT_URL=\nMANAGED_IDENTITY_CLIENT_ID=\nAPPLICATIONINSIGHTS_CONNECTION_STRING=\n",
        encoding="utf-8",
    )
    settings = CoreSettings.load()  # conftest has chdir'd into tmp_path
    assert settings.key_vault_url is None
    assert settings.managed_identity_client_id is None
    assert settings.applicationinsights_connection_string is None
