"""Control plane HTTP API (global): routing, onboarding, entitlements, stamp load.

Run locally: `uv run python -m va_control_plane serve --port 8090`.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, Query, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from va_control_plane import catalog
from va_control_plane.auth import Authenticator, Caller, Scope, build_authenticator
from va_control_plane.errors import OnboardingConflictError, PlacementError
from va_control_plane.onboarding import (
    PROVISION_RETRY,
    Onboarding,
    OnboardingResult,
    VenueOnboarding,
)
from va_control_plane.provisioning import (
    DatabaseStampProvisioner,
    StampConnection,
    StampProvisioner,
    UserGrant,
    entra_password,
    local_password,
)
from va_control_plane.schemas import (
    AddUserIn,
    EntitlementsOut,
    ErrorOut,
    OnboardingOut,
    OnboardVenueIn,
    RouteOut,
    StampOut,
    UserOut,
)
from va_control_plane.settings import ControlPlaneSettings
from va_core.errors import (
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    NotFoundError,
    VenueAgentsError,
)
from va_core.http import RetryPolicy
from va_core.secrets import EnvFileSecretStore
from va_core.telemetry import configure_telemetry
from va_db.credentials import (
    ENTRA_POOL_RECYCLE_SECONDS,
    EntraTokenPassword,
    check_deployed_tls,
)
from va_db.rls import CATALOG_ROLE
from va_db.session import (
    assert_rls_enforced,
    create_runtime_engine,
    create_sessionmaker,
)

_STATUS: dict[type[VenueAgentsError], int] = {
    AuthenticationError: status.HTTP_401_UNAUTHORIZED,
    AuthorizationError: status.HTTP_403_FORBIDDEN,
    NotFoundError: status.HTTP_404_NOT_FOUND,
    PlacementError: status.HTTP_409_CONFLICT,
    OnboardingConflictError: status.HTTP_409_CONFLICT,
}

_bearer = HTTPBearer(auto_error=False)


def require(scope: Scope) -> Callable[..., Awaitable[Caller]]:
    async def dependency(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    ) -> Caller:
        if credentials is None:
            raise AuthenticationError("bearer token required")
        authenticator: Authenticator = request.app.state.authenticator
        caller = await authenticator.authenticate(credentials.credentials)
        if scope not in caller.scopes:
            raise AuthorizationError(f"scope {scope} required")
        return caller

    return dependency


def _out(result: OnboardingResult) -> OnboardingOut:
    return OnboardingOut(
        tenant_id=result.tenant_id,
        venue_id=result.venue_id,
        stamp_id=result.stamp_id,
        job_id=result.job_id,
        status=result.status,
        error_code=result.error_code,
    )


def create_app(
    *,
    catalog_engine: AsyncEngine,
    provisioner: StampProvisioner,
    authenticator: Authenticator,
    check_roles: bool = True,
    docs: bool = True,
    on_shutdown: Sequence[Callable[[], Awaitable[None]]] = (),
    provision_retry: RetryPolicy = PROVISION_RETRY,
) -> FastAPI:
    """The app. Takes the engine and provisioner so tests can wire their own databases.

    `docs=False` hides /docs and /openapi.json (deployed); `on_shutdown` closes credentials.
    """
    sessions: async_sessionmaker[AsyncSession] = create_sessionmaker(catalog_engine)
    onboarding = Onboarding(sessions, provisioner, retry=provision_retry)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        if check_roles:
            await assert_rls_enforced(catalog_engine, CATALOG_ROLE)
        try:
            yield
        finally:
            await provisioner.aclose()
            await catalog_engine.dispose()
            for close in on_shutdown:
                await close()

    app = FastAPI(
        title="Venue Agents control plane",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.authenticator = authenticator
    responses: dict[int | str, dict[str, object]] = {
        401: {"model": ErrorOut},
        403: {"model": ErrorOut},
        404: {"model": ErrorOut},
    }

    @app.exception_handler(VenueAgentsError)
    async def _handle(_request: Request, exc: VenueAgentsError) -> JSONResponse:
        code = next((s for t, s in _STATUS.items() if isinstance(exc, t)), 500)
        message = exc.message if code < 500 else "internal error"
        headers = {"WWW-Authenticate": "Bearer"} if code == 401 else None
        return JSONResponse(
            ErrorOut(code=exc.code, message=message).model_dump(), status_code=code, headers=headers
        )

    @app.get("/route", response_model=RouteOut, responses=responses)
    async def get_route(
        venue_id: Annotated[UUID, Query()],
        _caller: Annotated[Caller, Depends(require(Scope.ROUTE_READ))],
    ) -> RouteOut:
        async with sessions() as session:
            found = await catalog.route(session, venue_id)
        return RouteOut(
            venue_id=found.venue_id,
            stamp_id=found.stamp_id,
            region=found.region,
            status=found.status,
        )

    @app.post(
        "/venues",
        response_model=OnboardingOut,
        status_code=status.HTTP_201_CREATED,
        responses={**responses, 409: {"model": ErrorOut}},
    )
    async def post_venue(
        body: OnboardVenueIn,
        _caller: Annotated[Caller, Depends(require(Scope.VENUES_WRITE))],
        idempotency_key: Annotated[
            str | None, Header(alias="Idempotency-Key", pattern=r"^[A-Za-z0-9_-]{1,128}$")
        ] = None,
    ) -> OnboardingOut:
        """Place and create a venue. `status` is `failed` if the stamp step failed: retry it.

        With an `Idempotency-Key`, a replay returns the first request's job (body not compared).
        """
        result = await onboarding.onboard(
            VenueOnboarding(
                venue_name=body.venue_name,
                timezone=body.timezone,
                region=body.region,
                plan=body.plan,
                ring=body.ring,
                tenant_id=body.tenant_id,
                tenant_name=body.tenant_name,
                stamp_id=body.stamp_id,
                entitlements=frozenset(body.entitlements),
                idempotency_key=idempotency_key,
            )
        )
        return _out(result)

    @app.post(
        "/venues/{venue_id}/onboarding/retry",
        response_model=OnboardingOut,
        responses={**responses, 409: {"model": ErrorOut}},
    )
    async def retry_onboarding(
        venue_id: UUID,
        _caller: Annotated[Caller, Depends(require(Scope.VENUES_WRITE))],
    ) -> OnboardingOut:
        return _out(await onboarding.retry(venue_id))

    @app.post(
        "/venues/{venue_id}/users",
        response_model=UserOut,
        status_code=status.HTTP_201_CREATED,
        responses=responses,
    )
    async def add_user(
        venue_id: UUID,
        body: AddUserIn,
        _caller: Annotated[Caller, Depends(require(Scope.VENUES_WRITE))],
    ) -> UserOut:
        """Add a user with a role on this venue (or tenant-wide) in the venue's stamp.

        Idempotent: an existing user (same `external_subject`) keeps their name and email.
        """
        async with sessions() as session:
            placed = await catalog.active_venue(session, venue_id)
        user_id = await provisioner.add_user(
            StampConnection.from_stamp(placed.stamp),
            UserGrant(
                tenant_id=placed.tenant_id,
                venue_id=placed.venue_id,
                external_subject=body.external_subject,
                email=body.email,
                display_name=body.display_name,
                role=body.role,
                tenant_wide=body.tenant_wide,
            ),
        )
        return UserOut(
            user_id=user_id, venue_id=venue_id, role=body.role, tenant_wide=body.tenant_wide
        )

    @app.get("/venues/{venue_id}/entitlements", response_model=EntitlementsOut, responses=responses)
    async def get_entitlements(
        venue_id: UUID,
        _caller: Annotated[Caller, Depends(require(Scope.ENTITLEMENTS_READ))],
    ) -> EntitlementsOut:
        async with sessions() as session:
            names = await catalog.entitlements(session, venue_id)
        return EntitlementsOut(venue_id=venue_id, entitlements=names)

    @app.get("/stamps", response_model=list[StampOut], responses=responses)
    async def get_stamps(
        _caller: Annotated[Caller, Depends(require(Scope.STAMPS_READ))],
    ) -> list[StampOut]:
        async with sessions() as session:
            loads = await catalog.stamp_loads(session)
        return [
            StampOut(
                id=s.id,
                region=s.region,
                ring=s.ring,
                status=s.status,
                capacity=s.capacity,
                venues=s.venues,
            )
            for s in loads
        ]

    return app


def app_from_settings() -> FastAPI:
    """Production wiring from settings (uvicorn `--factory`)."""
    settings = ControlPlaneSettings.load()
    configure_telemetry(settings)
    authenticator = build_authenticator(settings)
    if settings.catalog_database_url is None:
        raise ConfigurationError("CATALOG_DATABASE_URL is not set")
    catalog_url = settings.catalog_database_url.get_secret_value()
    check_deployed_tls(catalog_url, settings.environment)
    closers: list[Callable[[], Awaitable[None]]] = []
    if settings.environment.is_local:
        password_for = local_password(EnvFileSecretStore())
        catalog_engine = create_runtime_engine(
            catalog_url, connect_timeout=settings.database_connect_timeout
        )
    else:
        from azure.identity.aio import ManagedIdentityCredential

        credential = ManagedIdentityCredential(client_id=settings.managed_identity_client_id)
        closers.append(credential.close)
        entra = EntraTokenPassword(credential)
        password_for = entra_password(entra)
        catalog_engine = create_runtime_engine(
            catalog_url,
            connect_timeout=settings.database_connect_timeout,
            password=entra,
            pool_recycle=ENTRA_POOL_RECYCLE_SECONDS,
        )
    provisioner = DatabaseStampProvisioner(password_for, settings.environment)
    return create_app(
        catalog_engine=catalog_engine,
        provisioner=provisioner,
        authenticator=authenticator,
        docs=settings.environment.is_local,
        on_shutdown=closers,
    )
