"""OpenTelemetry setup: every span and log record carries tenant_id, venue_id and run_id.

Call `configure_telemetry()` once at process start. With an Application Insights connection
string it uses the Azure Monitor OpenTelemetry distro; without one (local dev, tests) it installs a
plain SDK tracer provider so spans still get tenant attributes but nothing is exported.

Log records are enriched and redacted by `TelemetryLogFilter` on every handler of the `va` root
logger, including the one the distro adds. Handler filters run after `extra=` is applied, so extras
are redacted too, and the context keys (tenant_id, venue_id, stamp_id, run_id) are reserved:
values passed in `extra=` are replaced by the real context. Use `get_logger(__name__)`; a handler
added later must go through `secure_handler()`.

Spans never carry raw URLs, query strings, bodies or exception messages (they can hold OAuth codes,
search terms or personal data). The distro's HTTP server and client instrumentations record full
URLs, so they are switched off; services wrap their ASGI app in `RequestTracingMiddleware`, which
records the method, route template and status, and only the exception type on errors.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, MutableMapping, Sequence

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from va_core.redaction import RedactingFilter
from va_core.settings import CoreSettings
from va_core.tenancy import current_run_id, current_tenant_or_none

LOGGER_ROOT = "va"
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s venue=%(venue_id)s run=%(run_id)s %(message)s"

_CONTEXT_KEYS = ("tenant_id", "venue_id", "stamp_id", "run_id")

_configured = False

# Distro instrumentations that record full URLs (with query strings) on spans: off (P0-02 M1).
_URL_RECORDING_INSTRUMENTATIONS = (
    "django",
    "fastapi",
    "flask",
    "httpx",
    "httpx2",
    "requests",
    "urllib",
    "urllib3",
)

type AsgiScope = MutableMapping[str, object]
type AsgiMessage = MutableMapping[str, object]
type AsgiReceive = Callable[[], Awaitable[AsgiMessage]]
type AsgiSend = Callable[[AsgiMessage], Awaitable[None]]
type AsgiApp = Callable[[AsgiScope, AsgiReceive, AsgiSend], Awaitable[None]]


def telemetry_attributes() -> dict[str, str]:
    """Tenant and run attributes for the current context (empty outside a scope)."""
    attrs: dict[str, str] = {}
    ctx = current_tenant_or_none()
    if ctx is not None:
        attrs["tenant_id"] = str(ctx.tenant_id)
        attrs["venue_id"] = str(ctx.venue_id)
        attrs["stamp_id"] = ctx.stamp_id
    run_id = current_run_id()
    if run_id is not None:
        attrs["run_id"] = run_id
    return attrs


class TenantSpanProcessor(SpanProcessor):
    """Adds tenant, venue and run IDs to every span when it starts."""

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        attrs = telemetry_attributes()
        if attrs:
            span.set_attributes(attrs)


def build_resource(settings: CoreSettings) -> Resource:
    return Resource.create(
        {
            "service.name": settings.service_name,
            "deployment.environment": settings.environment.value,
            "stamp_id": settings.stamp_id,
        }
    )


def build_tracer_provider(
    settings: CoreSettings, exporters: Sequence[SpanExporter] = ()
) -> TracerProvider:
    """SDK tracer provider with tenant enrichment. Exporters are for local debugging and tests."""
    provider = TracerProvider(resource=build_resource(settings))
    provider.add_span_processor(TenantSpanProcessor())
    for exporter in exporters:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider


class TelemetryLogFilter(RedactingFilter):
    """Redacts a record, then stamps it with the current tenant/run IDs (never caller-supplied)."""

    def filter(self, record: logging.LogRecord) -> bool:
        for key in _CONTEXT_KEYS:
            record.__dict__.pop(key, None)
        super().filter(record)
        # Set after redaction: UUID segments can look like TFNs or card numbers.
        record.__dict__.update(telemetry_attributes())
        return True


def secure_handler(handler: logging.Handler) -> logging.Handler:
    """Attach the telemetry filter to `handler` once."""
    if not any(isinstance(f, TelemetryLogFilter) for f in handler.filters):
        handler.addFilter(TelemetryLogFilter())
    return handler


def instrumentation_options() -> dict[str, dict[str, bool]]:
    """Distro instrumentations to switch off: Azure SDK (noisy Key Vault/identity spans) and every
    HTTP instrumentation that records raw URLs."""
    off = ("azure_sdk", *_URL_RECORDING_INSTRUMENTATIONS)
    return {name: {"enabled": False} for name in off}


def tag_current_span() -> None:
    """Add the current tenant/venue/run IDs to the active span.

    Call once the tenant is resolved (e.g. in a request dependency): the request span started
    before the tenant was known, so `TenantSpanProcessor` couldn't tag it.
    """
    attrs = telemetry_attributes()
    if attrs:
        trace.get_current_span().set_attributes(attrs)


class RequestTracingMiddleware:
    """ASGI middleware: one server span per HTTP request, without URLs, bodies or messages.

    The span is named `<METHOD> <route template>` (e.g. `POST /drafts/{draft_id}/approve`), so path
    parameters don't reach telemetry either. Unmatched routes are named `<METHOD> unmatched`.
    """

    def __init__(self, app: AsgiApp, tracer: trace.Tracer | None = None) -> None:
        self.app = app
        self._tracer = tracer or trace.get_tracer("va.http")

    async def __call__(self, scope: AsgiScope, receive: AsgiReceive, send: AsgiSend) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        method = str(scope.get("method", "GET"))
        status: list[int] = []

        async def send_wrapper(message: AsgiMessage) -> None:
            if message.get("type") == "http.response.start":
                code = message.get("status")
                if isinstance(code, int):
                    status.append(code)
            await send(message)

        with self._tracer.start_as_current_span(
            f"{method} request",
            kind=SpanKind.SERVER,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            span.set_attribute("http.request.method", method)
            try:
                await self.app(scope, receive, send_wrapper)
            except BaseException as exc:
                span.set_attribute("error.type", type(exc).__name__)
                span.set_status(StatusCode.ERROR)
                raise
            finally:
                route = _route_template(scope)
                span.update_name(f"{method} {route or 'unmatched'}")
                if route is not None:
                    span.set_attribute("http.route", route)
                if status:
                    span.set_attribute("http.response.status_code", status[0])
                    if status[0] >= 500:
                        span.set_status(StatusCode.ERROR)


def _route_template(scope: AsgiScope) -> str | None:
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else None


def _configure_azure_monitor(settings: CoreSettings, connection_string: str) -> None:
    # Imported lazily: the distro pulls in instrumentations we don't need in tests.
    from azure.monitor.opentelemetry import configure_azure_monitor  # pyright: ignore[reportUnknownVariableType]

    configure_azure_monitor(
        connection_string=connection_string,
        resource=build_resource(settings),
        span_processors=[TenantSpanProcessor()],
        logger_name=LOGGER_ROOT,
        instrumentation_options=instrumentation_options(),
    )


def configure_telemetry(settings: CoreSettings) -> None:
    """Set up tracing and logging for this process. Safe to call more than once."""
    global _configured
    if _configured:
        return
    root = logging.getLogger(LOGGER_ROOT)
    root.setLevel(settings.log_level)
    connection_string = settings.applicationinsights_connection_string
    if connection_string is not None:
        _configure_azure_monitor(settings, connection_string.get_secret_value())
    else:
        trace.set_tracer_provider(build_tracer_provider(settings))
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(_LOG_FORMAT, defaults={"venue_id": "-", "run_id": "-"})
        )
        root.addHandler(handler)
    for handler in root.handlers:
        secure_handler(handler)
    _configured = True


def get_tracer(name: str) -> trace.Tracer:
    return trace.get_tracer(name)


def get_logger(name: str) -> logging.Logger:
    """Logger under the exported `va` root, e.g. `get_logger(__name__)` → `va.va_core.http`."""
    return logging.getLogger(f"{LOGGER_ROOT}.{name}")
