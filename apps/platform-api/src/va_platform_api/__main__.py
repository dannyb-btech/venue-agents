"""Platform API commands.

    uv run python -m va_platform_api serve --port 8091
    uv run python -m va_platform_api dev-token --oid <user-oid> --tenant <tenant-uuid>
    uv run python -m va_platform_api openapi > apps/platform-api/openapi.json

`serve` runs on a selector event loop (psycopg's async driver can't use Windows' Proactor loop).
`dev-token` mints a local-only token (PLATFORM_DEV_TOKEN_KEY; refused outside local/test).
`openapi` prints the OpenAPI document the venue app's client is generated from.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from uuid import UUID

from va_core.errors import ConfigurationError

LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})


def openapi_document() -> dict[str, object]:
    """The OpenAPI document, built without a database, secrets or a token issuer."""
    from pydantic import SecretStr
    from sqlalchemy.ext.asyncio import create_async_engine

    from va_core.secrets import EnvFileSecretStore
    from va_core.settings import Environment
    from va_platform_api.api import create_app
    from va_platform_api.auth import DevTokenValidator
    from va_platform_api.events import InMemoryPublisher

    app = create_app(
        # Never connected: only the routes are needed.
        engine=create_async_engine("postgresql+psycopg://openapi@127.0.0.1/openapi"),
        validator=DevTokenValidator(SecretStr("o" * 32), Environment.TEST, "va_tenant_id"),
        publisher=InMemoryPublisher(),
        secret_store=EnvFileSecretStore(path="nonexistent.env"),
        stamp_id="openapi",
        oauth_redirect_uri="http://localhost:3000/connections/callback",
        check_roles=False,
    )
    return app.openapi()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="va_platform_api")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="run the API (uvicorn, selector event loop)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8091)
    token = commands.add_parser("dev-token", help="mint a local dev token")
    token.add_argument("--oid", required=True, help="the user's app_user.external_subject")
    token.add_argument("--tenant", required=True, type=UUID)
    token.add_argument("--lifetime", type=int, default=3600)
    commands.add_parser("openapi", help="print the OpenAPI document")
    args = parser.parse_args(argv)

    if args.command == "openapi":
        print(json.dumps(openapi_document(), indent=2, sort_keys=True))
        return 0

    from va_platform_api.settings import PlatformApiSettings

    settings = PlatformApiSettings.load()
    if args.command == "dev-token":
        from va_platform_api.auth import DevTokenValidator

        if settings.platform_dev_token_key is None or settings.hosted_in_azure:
            raise ConfigurationError("PLATFORM_DEV_TOKEN_KEY is not set (local/test only)")
        validator = DevTokenValidator(
            settings.platform_dev_token_key, settings.environment, settings.tenant_claim
        )
        print(validator.mint(subject=args.oid, tenant_id=args.tenant, lifetime=args.lifetime))
        return 0

    import uvicorn

    if settings.platform_dev_token_key is not None and args.host not in LOOPBACK:
        raise ConfigurationError("with PLATFORM_DEV_TOKEN_KEY set, serve on loopback only")
    # No uvicorn access log: it prints raw paths and query strings outside the redaction filter.
    # Requests are recorded by `RequestTracingMiddleware` (route templates only).
    config = uvicorn.Config(
        "va_platform_api.api:app_from_settings",
        factory=True,
        host=args.host,
        port=args.port,
        access_log=False,
    )
    asyncio.run(uvicorn.Server(config).serve(), loop_factory=asyncio.SelectorEventLoop)
    return 0


if __name__ == "__main__":
    sys.exit(main())
