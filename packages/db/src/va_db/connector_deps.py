"""One start-up path for every connector server (needs `va-core[mcp]`).

settings = ConnectorSettings.load()
deps = await build_connector_deps(settings, build_secret_store(settings), sessionmaker)
app = connector_app(build_connector_server(registry, deps),
                    allowed_hosts=settings.connector_allowed_hosts)
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from va_core.errors import ConfigurationError
from va_core.mcp.envelope import EnvelopeKeys, HmacEnvelopeVerifier
from va_core.mcp.ratelimit import Rate, RateLimiter
from va_core.mcp.registry import ToolRegistry
from va_core.mcp.server import ConnectorDeps
from va_core.mcp.settings import ConnectorSettings
from va_core.secrets import SecretStore
from va_db.connectors import PgRefreshLock, SqlConnectionStore, SqlDraftWriter


async def build_connector_deps(
    settings: ConnectorSettings,
    secret_store: SecretStore,
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    registry: ToolRegistry | None = None,
) -> ConnectorDeps:
    """Envelope verification for this connector and stamp, Postgres stores, rate limits."""
    system = settings.connector_system
    if registry is not None and registry.system != system:
        raise ConfigurationError(
            f"CONNECTOR_SYSTEM is {system!r} but the registry is for {registry.system!r}"
        )
    keys = await EnvelopeKeys.load(secret_store, settings.run_envelope_kids)
    return ConnectorDeps(
        verifier=HmacEnvelopeVerifier(keys, system=system, stamp_id=settings.stamp_id),
        secret_store=secret_store,
        connections=SqlConnectionStore(sessionmaker),
        drafts=SqlDraftWriter(sessionmaker),
        refresh_lock=PgRefreshLock(sessionmaker),
        limiter=RateLimiter(
            system,
            per_tenant=Rate(
                settings.connector_rate_per_tenant, settings.connector_burst_per_tenant
            ),
            per_system=Rate(
                settings.connector_rate_per_system, settings.connector_burst_per_system
            ),
        ),
    )
