import logging
from collections.abc import Iterator
from uuid import uuid4

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import SecretStr

import va_core.telemetry as telemetry
from va_core import CoreSettings, Environment, TenantContext, run_scope, tenant_scope
from va_core.redaction import REDACTED


@pytest.fixture
def settings() -> CoreSettings:
    return CoreSettings(
        environment=Environment.TEST, service_name="test-svc", stamp_id="canary-local"
    )


@pytest.fixture
def restore_logging(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(telemetry, "_configured", False)
    root = logging.getLogger(telemetry.LOGGER_ROOT)
    handlers = list(root.handlers)
    yield
    root.handlers = handlers


class Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def capture(restore_logging: None) -> Capture:
    handler = Capture()
    telemetry.secure_handler(handler)
    telemetry.secure_handler(handler)  # idempotent
    assert len(handler.filters) == 1
    root = logging.getLogger(telemetry.LOGGER_ROOT)
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    return handler


def test_spans_carry_tenant_and_run_ids(settings: CoreSettings) -> None:
    exporter = InMemorySpanExporter()
    tracer = telemetry.build_tracer_provider(settings, [exporter]).get_tracer("test")
    ctx = TenantContext(tenant_id=uuid4(), venue_id=uuid4(), stamp_id="canary-local")

    with tracer.start_as_current_span("outside"):
        pass
    with tenant_scope(ctx), run_scope("run-42"), tracer.start_as_current_span("inside"):
        pass

    outside, inside = exporter.get_finished_spans()
    assert outside.attributes is not None
    assert "venue_id" not in outside.attributes
    assert inside.attributes is not None
    assert inside.attributes["tenant_id"] == str(ctx.tenant_id)
    assert inside.attributes["venue_id"] == str(ctx.venue_id)
    assert inside.attributes["run_id"] == "run-42"
    assert inside.resource.attributes["service.name"] == "test-svc"
    assert inside.resource.attributes["stamp_id"] == "canary-local"


def test_log_records_are_enriched_and_redacted(capture: Capture) -> None:
    logger = telemetry.get_logger("tests")
    ctx = TenantContext(tenant_id=uuid4(), venue_id=uuid4(), stamp_id="canary-local")

    with tenant_scope(ctx), run_scope("run-7"):
        logger.info("token=%s", "abc123")
    logger.info("no context")

    inside, outside = capture.records
    assert inside.__dict__["venue_id"] == str(ctx.venue_id)
    assert inside.__dict__["tenant_id"] == str(ctx.tenant_id)
    assert inside.__dict__["run_id"] == "run-7"
    assert inside.getMessage() == f"token={REDACTED}"
    assert "venue_id" not in outside.__dict__


def test_extra_fields_are_redacted(capture: Capture) -> None:
    telemetry.get_logger("tests").info(
        "sync done",
        extra={
            "refresh_token": "SECRETVAL",
            "body": "Dear chef, full email body",
            "note": "tfn 123 456 782",
            "rows": 12,
        },
    )
    (record,) = capture.records
    assert record.__dict__["refresh_token"] == REDACTED
    assert record.__dict__["body"] == REDACTED
    assert record.__dict__["note"] == f"tfn {REDACTED}"
    assert record.__dict__["rows"] == 12


def test_context_keys_in_extra_cannot_be_spoofed(capture: Capture) -> None:
    logger = telemetry.get_logger("tests")
    ctx = TenantContext(tenant_id=uuid4(), venue_id=uuid4(), stamp_id="canary-local")
    logger.info("outside", extra={"venue_id": "spoofed", "run_id": "spoofed"})
    with tenant_scope(ctx):
        logger.info("inside", extra={"venue_id": "spoofed"})

    outside, inside = capture.records
    assert "venue_id" not in outside.__dict__
    assert "run_id" not in outside.__dict__
    assert inside.__dict__["venue_id"] == str(ctx.venue_id)


def test_exception_text_is_redacted(capture: Capture) -> None:
    try:
        raise RuntimeError("connect failed: Password=hunter2;Host=db")
    except RuntimeError:
        telemetry.get_logger("tests").exception("db error")
    (record,) = capture.records
    assert record.exc_text is not None
    assert "hunter2" not in record.exc_text
    assert "RuntimeError" in record.exc_text


def test_bad_format_string_does_not_raise(capture: Capture) -> None:
    telemetry.get_logger("tests").info("bad %s %s", "one")
    (record,) = capture.records
    assert record.getMessage().startswith("[unformattable log message")


def test_without_connection_string_azure_monitor_is_not_used(
    restore_logging: None, settings: CoreSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake_azure(_settings: CoreSettings, _connection_string: str) -> None:
        calls.append("azure")

    def fake_set_provider(_provider: object) -> None:
        calls.append("sdk")

    monkeypatch.setattr(telemetry, "_configure_azure_monitor", fake_azure)
    monkeypatch.setattr(trace, "set_tracer_provider", fake_set_provider)

    telemetry.configure_telemetry(settings)
    telemetry.configure_telemetry(settings)  # idempotent
    assert calls == ["sdk"]
    handlers = logging.getLogger(telemetry.LOGGER_ROOT).handlers
    assert all(
        any(isinstance(f, telemetry.TelemetryLogFilter) for f in h.filters) for h in handlers
    )


def test_with_connection_string_uses_azure_monitor(
    restore_logging: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    def fake_azure(_settings: CoreSettings, connection_string: str) -> None:
        seen.append(connection_string)

    monkeypatch.setattr(telemetry, "_configure_azure_monitor", fake_azure)
    settings = CoreSettings(
        environment=Environment.TEST,
        applicationinsights_connection_string=SecretStr("InstrumentationKey=abc"),
    )
    telemetry.configure_telemetry(settings)
    assert seen == ["InstrumentationKey=abc"]


def test_distro_url_recording_instrumentations_are_off() -> None:
    options = telemetry.instrumentation_options()
    for name in ("azure_sdk", "fastapi", "httpx", "requests", "urllib", "urllib3"):
        assert options[name] == {"enabled": False}


class _Route:
    path = "/drafts/{draft_id}/approve"


async def _call(
    middleware: telemetry.RequestTracingMiddleware, path: str, query: bytes = b""
) -> list[telemetry.AsgiMessage]:
    sent: list[telemetry.AsgiMessage] = []

    async def receive() -> telemetry.AsgiMessage:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: telemetry.AsgiMessage) -> None:
        sent.append(message)

    scope: telemetry.AsgiScope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "query_string": query,
    }
    await middleware(scope, receive, send)
    return sent


async def test_request_span_has_route_template_not_url(settings: CoreSettings) -> None:
    exporter = InMemorySpanExporter()
    tracer = telemetry.build_tracer_provider(settings, [exporter]).get_tracer("test")
    ctx = TenantContext(tenant_id=uuid4(), venue_id=uuid4(), stamp_id="canary-local")

    async def app(
        scope: telemetry.AsgiScope, _receive: telemetry.AsgiReceive, send: telemetry.AsgiSend
    ) -> None:
        scope["route"] = _Route()
        with tenant_scope(ctx):
            telemetry.tag_current_span()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    middleware = telemetry.RequestTracingMiddleware(app, tracer)
    draft = uuid4()
    await _call(middleware, f"/drafts/{draft}/approve", b"code=SECRETCODE")

    (span,) = exporter.get_finished_spans()
    assert span.name == "POST /drafts/{draft_id}/approve"
    attrs = dict(span.attributes or {})
    assert attrs["http.route"] == "/drafts/{draft_id}/approve"
    assert attrs["http.response.status_code"] == 200
    assert attrs["venue_id"] == str(ctx.venue_id)
    flat = repr(attrs) + span.name
    assert "SECRETCODE" not in flat
    assert str(draft) not in flat


async def test_request_span_records_only_exception_type(settings: CoreSettings) -> None:
    exporter = InMemorySpanExporter()
    tracer = telemetry.build_tracer_provider(settings, [exporter]).get_tracer("test")

    async def app(
        _scope: telemetry.AsgiScope, _receive: telemetry.AsgiReceive, _send: telemetry.AsgiSend
    ) -> None:
        raise ValueError("guest jane@example.com said hello")

    with pytest.raises(ValueError, match="jane"):
        await _call(telemetry.RequestTracingMiddleware(app, tracer), "/nowhere")

    (span,) = exporter.get_finished_spans()
    assert span.name == "POST unmatched"
    assert dict(span.attributes or {})["error.type"] == "ValueError"
    assert span.status.status_code == trace.StatusCode.ERROR
    assert span.status.description is None
    assert not span.events
