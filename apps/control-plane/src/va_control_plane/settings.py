"""Control plane settings (on top of `CoreSettings`)."""

from __future__ import annotations

from pydantic import Field, SecretStr

from va_core.settings import CoreSettings


class ControlPlaneSettings(CoreSettings):
    # The control plane is global, not a stamp: its telemetry is tagged `global`.
    stamp_id: str = Field(default="global", min_length=1)
    # Only the control plane reaches the catalog (a va_catalog member; ADR-0017). Not in
    # CoreSettings, so no stamp service can be pointed at it.
    catalog_database_url: SecretStr | None = None
    # Local/test only: a static bearer token standing in for Entra service tokens (P0-11/P0-12).
    control_plane_dev_token: SecretStr | None = None
