"""Platform API (per stamp): the venue app's backend (ADR-0019).

Every request: bearer token -> `Principal` (user + tenant claim) -> `Member` (tenant on this
stamp, active user, roles from the database) -> for venue endpoints, a `TenantContext` for the
venue named in `X-Venue-Id`. No endpoint takes a tenant ID.

Run locally: `uv run python -m va_platform_api serve --port 8091`.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, Path, Query, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from va_core.errors import (
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    NotFoundError,
    VenueAgentsError,
)
from va_core.payloads import payload_sha256
from va_core.secrets import SecretStore, build_secret_store
from va_core.telemetry import RequestTracingMiddleware, configure_telemetry, tag_current_span
from va_core.tenancy import Role, TenantContext, tenant_scope
from va_db.credentials import ENTRA_POOL_RECYCLE_SECONDS, EntraTokenPassword, check_deployed_tls
from va_db.models import (
    AgentRun,
    Connection,
    ConnectionStatus,
    Draft,
    DraftStatus,
    RunStatus,
    ToolCall,
)
from va_db.session import (
    assert_rls_enforced,
    create_runtime_engine,
    create_sessionmaker,
    tenant_session,
)
from va_platform_api.access import Member, resolve_member
from va_platform_api.auth import Principal, TokenValidator, build_validator
from va_platform_api.drafts import Decided, DraftDecisions, EditableTypes
from va_platform_api.errors import ConflictError, UnprocessableError
from va_platform_api.events import (
    ApprovalPublisher,
    InMemoryPublisher,
    ServiceBusApprovalPublisher,
)
from va_platform_api.oauth import OAuthFlows, OAuthProvider
from va_platform_api.schemas import (
    ApproveIn,
    ConnectionOut,
    DecisionOut,
    DraftOut,
    DraftSummaryOut,
    ErrorOut,
    MeOut,
    OAuthCallbackIn,
    OAuthStartOut,
    RejectIn,
    RunOut,
    RunSummaryOut,
    ToolCallOut,
    VenueOut,
)
from va_platform_api.settings import PlatformApiSettings

VENUE_HEADER = "X-Venue-Id"
SYSTEM_PATTERN = r"^[a-z][a-z0-9]{1,30}$"

READ_ROLES = frozenset(Role)
DECIDE_ROLES = frozenset({Role.OWNER, Role.MANAGER, Role.APPROVER})
CONNECT_ROLES = frozenset({Role.OWNER, Role.MANAGER})

_STATUS: dict[type[VenueAgentsError], int] = {
    AuthenticationError: status.HTTP_401_UNAUTHORIZED,
    AuthorizationError: status.HTTP_403_FORBIDDEN,
    NotFoundError: status.HTTP_404_NOT_FOUND,
    ConflictError: status.HTTP_409_CONFLICT,
    UnprocessableError: status.HTTP_422_UNPROCESSABLE_CONTENT,
}

_bearer = HTTPBearer(auto_error=False)


async def principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if credentials is None:
        raise AuthenticationError("bearer token required")
    validator: TokenValidator = request.app.state.validator
    return await validator.validate(credentials.credentials)


async def member(request: Request, caller: Annotated[Principal, Depends(principal)]) -> Member:
    return await resolve_member(request.app.state.sessions, caller, request.app.state.stamp_id)


async def venue_context(
    caller: Annotated[Member, Depends(member)],
    venue_id: Annotated[UUID, Header(alias=VENUE_HEADER, description="The venue to act on")],
) -> AsyncGenerator[TenantContext]:
    ctx = caller.context_for(venue_id)
    with tenant_scope(ctx):
        tag_current_span()
        yield ctx


def require(roles: frozenset[Role]) -> Callable[..., Awaitable[TenantContext]]:
    async def dependency(ctx: Annotated[TenantContext, Depends(venue_context)]) -> TenantContext:
        if not ctx.has_role(*roles):
            raise AuthorizationError("your role can't do this")
        return ctx

    return dependency


Reader = Annotated[TenantContext, Depends(require(READ_ROLES))]
Decider = Annotated[TenantContext, Depends(require(DECIDE_ROLES))]
Connector = Annotated[TenantContext, Depends(require(CONNECT_ROLES))]
System = Annotated[str, Path(pattern=SYSTEM_PATTERN)]


def _summary(draft: Draft) -> DraftSummaryOut:
    return DraftSummaryOut(
        id=draft.id,
        type=draft.type,
        status=DraftStatus(draft.status),
        run_id=draft.run_id,
        created_at=draft.created_at,
    )


def _decision(decided: Decided) -> DecisionOut:
    return DecisionOut(
        draft_id=decided.draft_id,
        approval_id=decided.approval_id,
        status=decided.status,
        payload_sha256=decided.payload_sha256,
        event_published=decided.event_published,
    )


def create_app(
    *,
    engine: AsyncEngine,
    validator: TokenValidator,
    publisher: ApprovalPublisher,
    secret_store: SecretStore,
    stamp_id: str,
    oauth_redirect_uri: str,
    oauth_providers: dict[str, OAuthProvider] | None = None,
    editable_types: EditableTypes | None = None,
    check_roles: bool = True,
    docs: bool = True,
    on_shutdown: Sequence[Callable[[], Awaitable[None]]] = (),
) -> FastAPI:
    """The app. Takes its collaborators so tests can wire their own database and fakes."""
    sessions: async_sessionmaker[AsyncSession] = create_sessionmaker(engine)
    decisions = DraftDecisions(sessions, publisher, editable_types or {})
    oauth = OAuthFlows(sessions, oauth_providers or {}, secret_store, oauth_redirect_uri)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        if check_roles:
            await assert_rls_enforced(engine)
        try:
            yield
        finally:
            await publisher.aclose()
            await validator.aclose()
            await secret_store.aclose()
            await engine.dispose()
            for close in on_shutdown:
                await close()

    app = FastAPI(
        title="Venue Agents Platform API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.add_middleware(RequestTracingMiddleware)  # pyright: ignore[reportArgumentType]
    app.state.validator = validator
    app.state.sessions = sessions
    app.state.stamp_id = stamp_id
    errors: dict[int | str, dict[str, object]] = {
        code: {"model": ErrorOut} for code in (401, 403, 404, 409, 422)
    }

    @app.exception_handler(VenueAgentsError)
    async def _handle(_request: Request, exc: VenueAgentsError) -> JSONResponse:
        code = next((s for t, s in _STATUS.items() if isinstance(exc, t)), 500)
        message = exc.message if code < 500 else "internal error"
        headers = {"WWW-Authenticate": "Bearer"} if code == 401 else None
        return JSONResponse(
            ErrorOut(code=exc.code if code < 500 else "internal", message=message).model_dump(),
            status_code=code,
            headers=headers,
        )

    @app.get("/me", response_model=MeOut, responses=errors)
    async def get_me(caller: Annotated[Member, Depends(member)]) -> MeOut:
        return MeOut(
            user_id=caller.user_id,
            display_name=caller.display_name,
            email=caller.email,
            venues=[
                VenueOut(venue_id=v.venue_id, name=v.name, roles=sorted(v.roles))
                for v in caller.venues
            ],
        )

    @app.get("/drafts", response_model=list[DraftSummaryOut], responses=errors)
    async def list_drafts(
        ctx: Reader,
        draft_status: Annotated[DraftStatus | None, Query(alias="status")] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> list[DraftSummaryOut]:
        query = select(Draft).where(Draft.venue_id == ctx.venue_id)
        if draft_status is not None:
            query = query.where(Draft.status == draft_status.value)
        async with tenant_session(sessions, ctx) as session:
            drafts = (
                await session.scalars(query.order_by(Draft.created_at.desc()).limit(limit))
            ).all()
        return [_summary(d) for d in drafts]

    @app.get("/drafts/{draft_id}", response_model=DraftOut, responses=errors)
    async def get_draft(draft_id: UUID, ctx: Reader) -> DraftOut:
        async with tenant_session(sessions, ctx) as session:
            draft = await session.scalar(
                select(Draft).where(Draft.id == draft_id, Draft.venue_id == ctx.venue_id)
            )
        if draft is None:
            raise NotFoundError("draft not found")
        return DraftOut(
            **_summary(draft).model_dump(),
            payload=draft.payload,
            payload_sha256=payload_sha256(draft.payload),
            reasoning_summary=draft.reasoning_summary,
            sources=draft.sources,
            editable=decisions.is_editable(draft.type),
        )

    @app.post("/drafts/{draft_id}/approve", response_model=DecisionOut, responses=errors)
    async def approve(draft_id: UUID, body: ApproveIn, ctx: Decider) -> DecisionOut:
        """Approve as shown (or with `edited_payload`, for editable types), then notify the
        executor. `event_published: false` means: call `/approval/publish` to retry."""
        return _decision(
            await decisions.approve(ctx, draft_id, body.shown_sha256, body.edited_payload)
        )

    @app.post("/drafts/{draft_id}/reject", response_model=DecisionOut, responses=errors)
    async def reject(draft_id: UUID, body: RejectIn, ctx: Decider) -> DecisionOut:
        return _decision(await decisions.reject(ctx, draft_id, body.shown_sha256, body.reason))

    @app.post("/drafts/{draft_id}/approval/publish", response_model=DecisionOut, responses=errors)
    async def publish_approval(draft_id: UUID, ctx: Decider) -> DecisionOut:
        return _decision(await decisions.republish(ctx, draft_id))

    @app.get("/connections", response_model=list[ConnectionOut], responses=errors)
    async def list_connections(ctx: Reader) -> list[ConnectionOut]:
        async with tenant_session(sessions, ctx) as session:
            rows = (
                await session.scalars(
                    select(Connection)
                    .where(Connection.venue_id == ctx.venue_id)
                    .order_by(Connection.system)
                )
            ).all()
        return [
            ConnectionOut(
                system=c.system,
                status=ConnectionStatus(c.status),
                scopes=c.scopes,
                updated_at=c.updated_at,
            )
            for c in rows
        ]

    @app.post("/connections/{system}/oauth/start", response_model=OAuthStartOut, responses=errors)
    async def oauth_start(system: System, ctx: Connector) -> OAuthStartOut:
        started = await oauth.start(ctx, system)
        return OAuthStartOut(authorize_url=started.authorize_url, expires_at=started.expires_at)

    @app.post(
        "/connections/{system}/oauth/callback",
        status_code=status.HTTP_204_NO_CONTENT,
        responses=errors,
    )
    async def oauth_callback(system: System, body: OAuthCallbackIn, ctx: Connector) -> None:
        """The venue app posts the provider's `code` and `state` here after the redirect."""
        await oauth.complete(ctx, system, body.code, body.state)

    @app.get("/runs", response_model=list[RunSummaryOut], responses=errors)
    async def list_runs(
        ctx: Reader, limit: Annotated[int, Query(ge=1, le=200)] = 50
    ) -> list[RunSummaryOut]:
        async with tenant_session(sessions, ctx) as session:
            runs = (
                await session.scalars(
                    select(AgentRun)
                    .where(AgentRun.venue_id == ctx.venue_id)
                    .order_by(AgentRun.started_at.desc())
                    .limit(limit)
                )
            ).all()
        return [_run_summary(r) for r in runs]

    @app.get("/runs/{run_id}", response_model=RunOut, responses=errors)
    async def get_run(run_id: UUID, ctx: Reader) -> RunOut:
        async with tenant_session(sessions, ctx) as session:
            run = await session.scalar(
                select(AgentRun).where(AgentRun.id == run_id, AgentRun.venue_id == ctx.venue_id)
            )
            if run is None:
                raise NotFoundError("run not found")
            calls = (
                await session.scalars(
                    select(ToolCall).where(ToolCall.run_id == run.id).order_by(ToolCall.created_at)
                )
            ).all()
            drafts = (
                await session.scalars(
                    select(Draft)
                    .where(Draft.run_id == run.id, Draft.venue_id == ctx.venue_id)
                    .order_by(Draft.created_at)
                )
            ).all()
        return RunOut(
            **_run_summary(run).model_dump(),
            tool_calls=[
                ToolCallOut(
                    tool=c.tool,
                    category=c.category,
                    status=c.status,
                    duration_ms=c.duration_ms,
                    created_at=c.created_at,
                )
                for c in calls
            ],
            drafts=[_summary(d) for d in drafts],
        )

    return app


def _run_summary(run: AgentRun) -> RunSummaryOut:
    return RunSummaryOut(
        id=run.id,
        agent=run.agent,
        status=RunStatus(run.status),
        model=run.model,
        prompt_version=run.prompt_version,
        error_code=run.error_code,
        started_at=run.started_at,
        ended_at=run.ended_at,
    )


def _publisher(settings: PlatformApiSettings) -> ApprovalPublisher:
    if settings.servicebus_namespace is not None:
        return ServiceBusApprovalPublisher.from_namespace(
            settings.servicebus_namespace,
            settings.approvals_topic,
            settings.managed_identity_client_id,
        )
    if settings.environment.is_local:
        if settings.servicebus_connection_string is not None:
            return ServiceBusApprovalPublisher.from_connection_string(
                settings.servicebus_connection_string.get_secret_value(), settings.approvals_topic
            )
        return InMemoryPublisher()
    raise ConfigurationError(
        f"SERVICEBUS_NAMESPACE is required when ENVIRONMENT={settings.environment}"
    )


def app_from_settings() -> FastAPI:
    """Production wiring from settings (uvicorn `--factory`)."""
    settings = PlatformApiSettings.load()
    configure_telemetry(settings)
    validator = build_validator(settings)
    if settings.database_url is None:
        raise ConfigurationError("DATABASE_URL is not set")
    url = settings.database_url.get_secret_value()
    check_deployed_tls(url, settings.environment)
    closers: list[Callable[[], Awaitable[None]]] = []
    if settings.environment.is_local:
        engine = create_runtime_engine(url, connect_timeout=settings.database_connect_timeout)
    else:
        from azure.identity.aio import ManagedIdentityCredential

        credential = ManagedIdentityCredential(client_id=settings.managed_identity_client_id)
        closers.append(credential.close)
        engine = create_runtime_engine(
            url,
            connect_timeout=settings.database_connect_timeout,
            password=EntraTokenPassword(credential),
            pool_recycle=ENTRA_POOL_RECYCLE_SECONDS,
        )
    return create_app(
        engine=engine,
        validator=validator,
        publisher=_publisher(settings),
        secret_store=build_secret_store(settings),
        stamp_id=settings.stamp_id,
        oauth_redirect_uri=f"{str(settings.venue_app_url).rstrip('/')}/connections/callback",
        docs=settings.environment.is_local,
        on_shutdown=closers,
    )
