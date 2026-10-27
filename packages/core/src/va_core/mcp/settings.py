"""Settings for a connector MCP server process."""

from __future__ import annotations

from typing import Self

from pydantic import Field, model_validator

from va_core.settings import CoreSettings

LOCAL_DEFAULTS = {"stamp_id": "canary-local", "run_envelope_kids": ["local"]}


class ConnectorSettings(CoreSettings):
    connector_system: str = Field(pattern=r"^[a-z][a-z0-9]{0,31}$")
    # Envelope keys are secrets `run-envelope-key-<kid>`; the first kid signs, all verify.
    run_envelope_kids: list[str] = Field(default_factory=lambda: ["local"], min_length=1)
    # Host headers the MCP endpoint accepts (DNS-rebinding protection); `host:*` allows any port.
    connector_allowed_hosts: list[str] = Field(
        default_factory=lambda: ["127.0.0.1:*", "localhost:*"], min_length=1
    )
    connector_rate_per_tenant: float = Field(default=5.0, gt=0)
    connector_burst_per_tenant: int = Field(default=20, ge=1)
    connector_rate_per_system: float = Field(default=50.0, gt=0)
    connector_burst_per_system: int = Field(default=200, ge=1)

    @model_validator(mode="after")
    def _check_connector_settings(self) -> Self:
        if any(host.split(":")[0] in {"", "*"} for host in self.connector_allowed_hosts):
            raise ValueError("CONNECTOR_ALLOWED_HOSTS must name hosts; wildcards are not allowed")
        if self.environment.is_local:
            return self
        # Deployed connectors must not verify envelopes against local defaults.
        defaults = [name for name, value in LOCAL_DEFAULTS.items() if getattr(self, name) == value]
        if defaults:
            raise ValueError(
                f"{', '.join(defaults)} must be set when ENVIRONMENT={self.environment}"
            )
        if self.connector_system == "sample":
            raise ValueError("the sample connector is for tests only")
        return self
