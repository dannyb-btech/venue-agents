"""Operator commands. Stamps are infrastructure, so they're registered here, not through the API.

    uv run python -m va_control_plane register-stamp --id canary-local --region australiaeast \\
        --ring canary --capacity 250 --db-host 127.0.0.1 --db-name venue_agents \\
        --db-user va_provision_local --db-sslmode disable

Uses CATALOG_ADMIN_URL (the catalog owner): `va_catalog` can't write `stamp`.

    uv run python -m va_control_plane serve --port 8090

Runs the API on a selector event loop: psycopg's async driver can't use Windows' default Proactor
loop, which plain `uvicorn` picks on Windows.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from sqlalchemy import create_engine, pool
from sqlalchemy.dialects.postgresql import insert

from va_core.errors import ConfigurationError
from va_db.catalog.models import STAMP_ID, Ring, SslMode, Stamp, StampStatus
from va_db.credentials import AZURE_POSTGRES_HOST_SUFFIX
from va_db.settings import MigrationSettings

LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})


def _stamp_id(value: str) -> str:
    if not STAMP_ID.fullmatch(value) or len(value) > 63:
        raise argparse.ArgumentTypeError("lowercase letters, digits and single dashes")
    return value


def register_stamp(admin_url: str, values: dict[str, object]) -> None:
    """Insert or update a stamp (capacity, status and connection details can change)."""
    engine = create_engine(admin_url, poolclass=pool.NullPool)
    try:
        with engine.begin() as conn:
            statement = insert(Stamp).values(**values)
            updates = {k: statement.excluded[k] for k in values if k != "id"}
            conn.execute(statement.on_conflict_do_update(index_elements=["id"], set_=updates))
    finally:
        engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="va_control_plane")
    commands = parser.add_subparsers(dest="command", required=True)
    reg = commands.add_parser("register-stamp", help="add or update a stamp in the catalog")
    reg.add_argument("--id", required=True, type=_stamp_id)
    reg.add_argument("--region", required=True)
    reg.add_argument("--ring", required=True, choices=[r.value for r in Ring])
    reg.add_argument("--capacity", required=True, type=int)
    reg.add_argument(
        "--status", default=StampStatus.ACTIVE.value, choices=[s.value for s in StampStatus]
    )
    reg.add_argument("--db-host", required=True)
    reg.add_argument("--db-port", type=int, default=5432)
    reg.add_argument("--db-name", required=True)
    reg.add_argument("--db-user", required=True, help="a va_provision member on that server")
    reg.add_argument(
        "--db-sslmode", default=SslMode.VERIFY_FULL.value, choices=[m.value for m in SslMode]
    )
    serve = commands.add_parser("serve", help="run the API (uvicorn, selector event loop)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8090)
    args = parser.parse_args(argv)

    if args.command == "serve":
        import uvicorn

        from va_control_plane.settings import ControlPlaneSettings

        serving = ControlPlaneSettings.load()
        if serving.control_plane_dev_token is not None and args.host not in LOOPBACK:
            raise ConfigurationError("with CONTROL_PLANE_DEV_TOKEN set, serve on loopback only")

        config = uvicorn.Config(
            "va_control_plane.api:app_from_settings", factory=True, host=args.host, port=args.port
        )
        asyncio.run(uvicorn.Server(config).serve(), loop_factory=asyncio.SelectorEventLoop)
        return 0

    settings = MigrationSettings.load()
    if settings.catalog_admin_url is None:
        raise ConfigurationError("CATALOG_ADMIN_URL is not set")
    if not settings.environment.is_local and args.db_sslmode != SslMode.VERIFY_FULL:
        raise ConfigurationError("--db-sslmode must be verify-full outside local")
    azure_host = args.db_host.endswith(AZURE_POSTGRES_HOST_SUFFIX)
    if not settings.environment.is_local and not azure_host:
        raise ConfigurationError(
            f"--db-host must be an Azure Postgres host (*{AZURE_POSTGRES_HOST_SUFFIX})"
        )
    register_stamp(
        settings.catalog_admin_url.get_secret_value(),
        {
            "id": args.id,
            "region": args.region,
            "ring": args.ring,
            "capacity": args.capacity,
            "status": args.status,
            "db_host": args.db_host,
            "db_port": args.db_port,
            "db_name": args.db_name,
            "db_provision_user": args.db_user,
            "db_sslmode": args.db_sslmode,
        },
    )
    print(f"registered stamp {args.id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
