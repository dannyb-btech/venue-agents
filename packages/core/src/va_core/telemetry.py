"""OpenTelemetry setup: every span and log record carries tenant_id, venue_id and run_id.

Call `configure_telemetry()` once at process start. With an Application Insights connection
string it uses the Azure Monitor OpenTelemetry distro; without one (local dev, tests) it installs a
plain SDK tracer provider so spans still get tenant attributes but nothing is exported.

Log records are enriched and redacted by `TelemetryLogFilter` on every handler of the `va` root
logger, including the one the distro adds. Handler filters run after `extra=` is applied, so extras
are redacted too, and the context keys (tenant_id, venue_id, stamp_id, run_id) are reserved:
values passed in `extra=` are replaced by the real context. Use `get_logger(__name__)`; a handler
added later must go through `secure_handler()`.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter

from va_core.redaction import RedactingFilter
from va_core.settings import CoreSettings
from va_core.tenancy import current_run_id, current_tenant_or_none

LOGGER_ROOT = "va"
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s venue=%(venue_id)s run=%(run_id)s %(message)s"

_CONTEXT_KEYS = ("tenant_id", "venue_id", "stamp_id", "run_id")

_configured = False


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


def _configure_azure_monitor(settings: CoreSettings, connection_string: str) -> None:
    # Imported lazily: the distro pulls in instrumentations we don't need in tests.
    from azure.monitor.opentelemetry import configure_azure_monitor  # pyright: ignore[reportUnknownVariableType]

    configure_azure_monitor(
        connection_string=connection_string,
        resource=build_resource(settings),
        span_processors=[TenantSpanProcessor()],
        logger_name=LOGGER_ROOT,
        # Key Vault and identity calls would otherwise create noisy dependency spans.
        instrumentation_options={"azure_sdk": {"enabled": False}},
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
