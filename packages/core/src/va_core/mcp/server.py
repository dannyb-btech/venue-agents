"""MCP server base for connectors: envelope middleware, allow-list, rate limits, tool wrappers.

Every request must carry a valid run envelope (`X-VA-Run-Envelope`) for this connector and stamp.
The middleware verifies it, then runs the request inside `tenant_scope` and `run_scope`:
- only the MCP methods connectors need are served (`ALLOWED_METHODS`); resources, prompts,
  completions or extension methods are refused, so nothing can run outside the tool rules;
- `tools/list` returns only the tools in the envelope's allow-list (uncacheable);
- `tools/call` is refused unless the tool is registered *and* allow-listed, its raw arguments
  validate against the tool's arguments model (unknown fields such as `tenant_id` are refused, not
  silently dropped), and the rate limiter has room.

Errors returned to the caller (and so to the model) carry a code, never exception text, argument
values or secret names; details go to the (redacted) logs.

The HTTP app is stateless (`stateless_http=True`): no MCP session outlives one HTTP request, so no
context set for one tenant can be seen by another tenant's request.
"""

from __future__ import annotations

import inspect
import time
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID

from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError
from mcp_types import (
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    CallToolResult,
    ListToolsResult,
    TextContent,
    ToolAnnotations,
)
from pydantic import BaseModel, ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from va_core.credentials import (
    ConnectionInfo,
    ConnectionStore,
    RefreshLock,
    TenantCredentials,
    TokenCache,
)
from va_core.errors import EnvelopeError, RateLimitedError, VenueAgentsError
from va_core.mcp.context import DraftCreator, ToolContext
from va_core.mcp.drafts import DraftWriter, NewDraft
from va_core.mcp.envelope import ENVELOPE_HEADER, EnvelopeVerifier, RunEnvelope
from va_core.mcp.ratelimit import RateLimiter
from va_core.mcp.registry import ToolRegistry, ToolSpec
from va_core.secrets import SecretStore, TenantSecrets
from va_core.telemetry import get_logger, get_tracer
from va_core.tenancy import run_scope, tenant_scope
from va_core.tools import ToolCategory

logger = get_logger(__name__)
tracer = get_tracer(__name__)

MCP_PATH = "/mcp"
ALLOWED_METHODS = frozenset({"initialize", "ping", "server/discover", "tools/list", "tools/call"})
_envelope: ContextVar[RunEnvelope | None] = ContextVar("va_run_envelope", default=None)


@dataclass(frozen=True)
class ConnectorDeps:
    """Built once at start-up. Tools never see this; they get a per-call `ToolContext`."""

    verifier: EnvelopeVerifier
    secret_store: SecretStore
    connections: ConnectionStore
    drafts: DraftWriter
    refresh_lock: RefreshLock
    limiter: RateLimiter
    tokens: TokenCache = field(default_factory=TokenCache)


def _tool_error(code: str, **details: object) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=code)],
        structured_content={"error": code, **details},
        is_error=True,
    )


def _argument_problems(exc: ValidationError) -> str:
    """Names of our own fields that failed; unknown fields (caller-chosen text) are not echoed."""
    problems: set[str] = set()
    for err in exc.errors():
        if err["type"] == "extra_forbidden":
            problems.add("unexpected field")
        else:
            problems.add(".".join(str(p) for p in err["loc"]) or "arguments")
    return ", ".join(sorted(problems))


class TenantEnvelopeMiddleware:
    """Verifies the run envelope on every request and scopes the request to its tenant."""

    def __init__(self, registry: ToolRegistry, deps: ConnectorDeps) -> None:
        self._registry = registry
        self._deps = deps

    async def __call__(
        self, ctx: ServerRequestContext[Any, Any], call_next: CallNext
    ) -> HandlerResult:
        if ctx.request_id is None:
            # Notifications have no response and can't call tools.
            return await call_next(ctx)
        request = ctx.request
        headers = cast(Mapping[str, str] | None, getattr(request, "headers", None))
        try:
            envelope = self._deps.verifier.verify(
                headers.get(ENVELOPE_HEADER) if headers is not None else None
            )
        except EnvelopeError as exc:
            logger.warning("connector request refused: %s", exc.message)
            raise MCPError(code=INVALID_REQUEST, message=exc.message) from None
        if ctx.method not in ALLOWED_METHODS:
            raise MCPError(code=METHOD_NOT_FOUND, message="method not available on connectors")
        token = _envelope.set(envelope)
        try:
            with tenant_scope(envelope.to_context()), run_scope(str(envelope.run_id)):
                if ctx.method == "tools/call":
                    return await self._call(envelope, ctx, call_next)
                result = await call_next(ctx)
                if ctx.method == "tools/list":
                    return self._filter_list(envelope, result)
                return result
        finally:
            _envelope.reset(token)

    async def _call(
        self, envelope: RunEnvelope, ctx: ServerRequestContext[Any, Any], call_next: CallNext
    ) -> HandlerResult:
        params = ctx.params
        name = params.get("name") if params is not None else None
        spec = self._registry.get(name) if isinstance(name, str) else None
        tool = spec.name if spec is not None else "unknown"
        with tracer.start_as_current_span(
            "connector.tool_call",
            attributes={
                "va.connector": self._registry.system,
                "va.tool": tool,
                "va.agent": envelope.agent,
            },
        ) as span:
            started = time.monotonic()
            refusal = self._check_call(envelope, spec, params)
            if refusal is not None:
                span.set_attribute("va.outcome", "refused")
                logger.info("tool call refused: %s", refusal.structured_content)
                return refusal
            span.set_attribute("va.category", cast(ToolSpec, spec).category.value)
            result = await call_next(ctx)
            outcome = "error" if getattr(result, "is_error", False) else "ok"
            if isinstance(result, dict) and cast(dict[str, object], result).get("isError"):
                outcome = "error"
            span.set_attribute("va.outcome", outcome)
            span.set_attribute("va.duration_ms", int((time.monotonic() - started) * 1000))
            return result

    def _check_call(
        self, envelope: RunEnvelope, spec: ToolSpec | None, params: Mapping[str, Any] | None
    ) -> CallToolResult | None:
        if spec is None or spec.name not in envelope.tools:
            return _tool_error("tool_not_available")
        arguments = params.get("arguments") if params is not None else None
        try:
            spec.args_model.model_validate(arguments or {})
        except ValidationError as exc:
            return _tool_error("invalid_arguments", fields=_argument_problems(exc))
        try:
            self._deps.limiter.acquire(envelope.tenant_id)
        except RateLimitedError as exc:
            return _tool_error(exc.code, retry_after=exc.retry_after)
        return None

    @staticmethod
    def _filter_list(envelope: RunEnvelope, result: HandlerResult) -> HandlerResult:
        # The list differs per envelope: immediately stale and never shared between callers.
        if isinstance(result, ListToolsResult):
            allowed = [tool for tool in result.tools if tool.name in envelope.tools]
            return result.model_copy(
                update={"tools": allowed, "ttl_ms": 0, "cache_scope": "private"}
            )
        if isinstance(result, dict) and isinstance(result.get("tools"), list):
            tools = cast(list[object], result["tools"])
            filtered = dict(result)
            if "ttlMs" in filtered or "cacheScope" in filtered:
                filtered.update({"ttlMs": 0, "cacheScope": "private"})
            filtered["tools"] = [
                tool
                for tool in tools
                if isinstance(tool, dict)
                and cast(dict[str, object], tool).get("name") in envelope.tools
            ]
            return filtered
        # Fail closed: an unknown shape could list tools the run may not use.
        raise MCPError(code=INVALID_REQUEST, message="unexpected tools/list result")


def _tool_context(
    spec: ToolSpec, registry: ToolRegistry, deps: ConnectorDeps, envelope: RunEnvelope
) -> ToolContext:
    """Everything bound to the envelope's tenant, venue and run (nothing can be re-pointed)."""
    tenant = envelope.to_context()
    secrets = TenantSecrets(deps.secret_store, tenant)
    run_id: UUID = envelope.run_id

    async def lookup() -> ConnectionInfo | None:
        return await deps.connections.get(tenant, registry.system)

    create_draft: DraftCreator | None = None
    if spec.category is ToolCategory.DRAFT:

        async def create(draft: NewDraft) -> UUID:
            return await deps.drafts.create(tenant, run_id, draft)

        create_draft = create
    return ToolContext(
        tenant=tenant,
        run_id=run_id,
        agent=envelope.agent,
        system=registry.system,
        tool=spec.name,
        category=spec.category,
        secrets=secrets,
        credentials=TenantCredentials(
            tenant,
            registry.system,
            lookup=lookup,
            secrets=secrets,
            tokens=deps.tokens,
            refresh_lock=deps.refresh_lock,
        ),
        _create_draft=create_draft,
    )


def _make_tool(spec: ToolSpec, registry: ToolRegistry, deps: ConnectorDeps) -> Any:
    """An MCP tool function whose signature mirrors the arguments model (for the schema)."""

    async def call(**kwargs: object) -> BaseModel:
        envelope = _envelope.get()
        if envelope is None or spec.name not in envelope.tools:
            raise ToolError("tool_not_available")
        try:
            args = spec.args_model.model_validate(kwargs)
            return await spec.handler(args, _tool_context(spec, registry, deps, envelope))
        except VenueAgentsError as exc:
            # The message can name secrets or URLs: log it (redacted), return the code only.
            logger.info("tool %s failed: %s: %s", spec.name, exc.code, exc.message)
            raise ToolError(exc.code) from exc
        except ValidationError as exc:
            raise ToolError("invalid_arguments") from exc
        except Exception as exc:
            logger.exception("tool %s raised an unexpected error", spec.name)
            raise ToolError("internal_error") from exc

    parameters = [
        inspect.Parameter(
            name,
            inspect.Parameter.KEYWORD_ONLY,
            annotation=info.annotation,
            default=inspect.Parameter.empty if info.is_required() else info.get_default(),
        )
        for name, info in spec.args_model.model_fields.items()
    ]
    call.__signature__ = inspect.Signature(parameters, return_annotation=spec.result_model)  # type: ignore[attr-defined]
    call.__annotations__ = {
        **{name: info.annotation for name, info in spec.args_model.model_fields.items()},
        "return": spec.result_model,
    }
    call.__name__ = spec.name
    return call


def build_connector_server(registry: ToolRegistry, deps: ConnectorDeps) -> MCPServer:
    if len(registry) == 0:
        raise VenueAgentsError(f"connector {registry.system!r} registers no tools")
    server: MCPServer = MCPServer(
        f"va-connector-{registry.system}",
        middleware=[TenantEnvelopeMiddleware(registry, deps)],
        warn_on_duplicate_tools=True,
    )
    for spec in registry:
        server.add_tool(
            _make_tool(spec, registry, deps),
            name=spec.name,
            description=spec.description,
            annotations=ToolAnnotations(
                read_only_hint=spec.category is ToolCategory.READ,
                destructive_hint=False,
                idempotent_hint=spec.category is ToolCategory.READ,
                open_world_hint=spec.category is ToolCategory.READ,
            ),
            structured_output=True,
        )

    @server.custom_route("/healthz", methods=["GET"])
    async def healthz(_request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        return JSONResponse({"status": "ok"})

    return server


def connector_app(server: MCPServer, *, allowed_hosts: list[str]) -> Starlette:
    """The stateless streamable-HTTP ASGI app (its lifespan runs the MCP session manager)."""
    if not allowed_hosts or any(host.split(":")[0] in {"", "*"} for host in allowed_hosts):
        raise VenueAgentsError("allowed_hosts must name hosts; wildcards are not allowed")
    return server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        stateless_http=True,
        # SSE responses: in mcp 2.3.0 stateless JSON responses arrive empty under uvicorn (the ASGI
        # test transport hides it; see lessons.md). Clients accept both per the spec.
        json_response=False,
        transport_security=TransportSecuritySettings(allowed_hosts=allowed_hosts),
    )
