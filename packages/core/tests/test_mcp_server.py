"""Connector server end to end over streamable HTTP: envelope, allow-list, tenant scope, drafts."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

import httpx
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, ConfigDict

from va_core.credentials import ConnectionInfo
from va_core.errors import AuthorizationError, VenueAgentsError
from va_core.mcp.context import ToolContext
from va_core.mcp.drafts import NewDraft
from va_core.mcp.envelope import ENVELOPE_HEADER, EnvelopeKeys, HmacEnvelopeVerifier, issue_envelope
from va_core.mcp.ratelimit import Rate, RateLimiter
from va_core.mcp.registry import ToolRegistry
from va_core.mcp.server import ConnectorDeps, build_connector_server, connector_app
from va_core.mcp.testing import (
    LocalRefreshLock,
    MemoryConnectionStore,
    MemoryDraftWriter,
    MemorySecretStore,
    make_keys,
    sample_registry,
)
from va_core.secrets import venue_secret_name
from va_core.tenancy import TenantContext, current_run_id, current_tenant

ALL_TOOLS = ("sample_get_note", "sample_draft_note")
DEFAULT_RATE = Rate(100.0, 100)
HOST = "http://testserver"


def _ctx() -> TenantContext:
    return TenantContext(tenant_id=uuid4(), venue_id=uuid4(), stamp_id="canary-local")


def vendor(request: httpx.Request) -> httpx.Response:
    """Fake notes API: echoes which API key called it."""
    auth = request.headers.get("authorization", "")
    return httpx.Response(200, json={"title": "Menu", "body": f"seen {auth}"})


@dataclasses.dataclass
class Harness:
    keys: EnvelopeKeys
    deps: ConnectorDeps
    secrets: MemorySecretStore
    connections: MemoryConnectionStore
    drafts: MemoryDraftWriter
    app: Any

    def tenant(self, key: str = "key") -> TenantContext:
        ctx = _ctx()
        name = venue_secret_name(ctx, "sample", "api-key")
        self.secrets.values[name] = f"{key}-{ctx.tenant_id}"
        self.connections.add(ctx, ConnectionInfo(system="sample", status="active", secret_ref=name))
        return ctx

    def envelope(
        self, ctx: TenantContext, *, run_id: UUID | None = None, tools: Sequence[str] = ALL_TOOLS
    ) -> str:
        return issue_envelope(
            self.keys, ctx, system="sample", run_id=run_id or uuid4(), agent="hello", tools=tools
        )

    @asynccontextmanager
    async def client(self, envelope: str | None) -> AsyncGenerator[Client]:
        headers = {ENVELOPE_HEADER: envelope} if envelope is not None else {}
        http = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=self.app), base_url=HOST, headers=headers
        )
        async with http, Client(streamable_http_client(f"{HOST}/mcp", http_client=http)) as client:
            yield client


def _deps(
    keys: EnvelopeKeys, *, per_tenant: Rate = DEFAULT_RATE
) -> tuple[ConnectorDeps, MemorySecretStore, MemoryConnectionStore, MemoryDraftWriter]:
    secrets, connections, drafts = MemorySecretStore(), MemoryConnectionStore(), MemoryDraftWriter()
    deps = ConnectorDeps(
        verifier=HmacEnvelopeVerifier(keys, system="sample", stamp_id="canary-local"),
        secret_store=secrets,
        connections=connections,
        drafts=drafts,
        refresh_lock=LocalRefreshLock(),
        limiter=RateLimiter("sample", per_tenant=per_tenant, per_system=Rate(1000.0, 1000)),
    )
    return deps, secrets, connections, drafts


@asynccontextmanager
async def _serve(registry: ToolRegistry, **kw: Any) -> AsyncGenerator[Harness]:
    keys = make_keys()
    deps, secrets, connections, drafts = _deps(keys, **kw)
    server = build_connector_server(registry, deps)
    app = connector_app(server, allowed_hosts=["testserver"])
    async with server.session_manager.run():
        yield Harness(keys, deps, secrets, connections, drafts, app)


def _sample() -> ToolRegistry:
    return sample_registry(httpx.MockTransport(vendor))


def _text(result: Any) -> str:
    return " ".join(getattr(c, "text", "") for c in result.content)


async def _raw_call(
    harness: Harness, envelope: str | None, method: str, params: dict[str, Any]
) -> Any:
    """One JSON-RPC request without the SDK client, to see the server's exact answer."""
    headers = {
        "accept": "application/json, text/event-stream",
        "mcp-protocol-version": "2025-06-18",
    }
    if envelope is not None:
        headers[ENVELOPE_HEADER] = envelope
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=harness.app), base_url=HOST
    ) as http:
        response = await http.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            headers=headers,
        )
    # Responses are server-sent events; the JSON-RPC message is the last `data:` line.
    data = [line for line in response.text.splitlines() if line.startswith("data:")]
    assert data, f"no SSE data in response ({response.status_code})"
    return json.loads(data[-1].removeprefix("data:"))


def _bad_envelopes(harness: Harness) -> dict[str, tuple[str | None, str]]:
    ctx = harness.tenant()
    run = uuid4()
    return {
        "missing": (None, "run envelope is missing"),
        "garbage": ("not-a-token", "run envelope is malformed"),
        "other connector": (
            issue_envelope(
                harness.keys,
                ctx,
                system="gmail",
                run_id=run,
                agent="hello",
                tools=["gmail_search_threads"],
            ),
            "run envelope is invalid (InvalidAudienceError)",
        ),
        "other key": (
            issue_envelope(
                make_keys(), ctx, system="sample", run_id=run, agent="hello", tools=list(ALL_TOOLS)
            ),
            "run envelope is invalid (InvalidSignatureError)",
        ),
        "other stamp": (
            issue_envelope(
                harness.keys,
                ctx.model_copy(update={"stamp_id": "general-01"}),
                system="sample",
                run_id=run,
                agent="hello",
                tools=list(ALL_TOOLS),
            ),
            "run envelope is for another stamp",
        ),
    }


@pytest.mark.parametrize(
    "case", ["missing", "garbage", "other connector", "other key", "other stamp"]
)
@pytest.mark.parametrize(
    ("method", "params"),
    [
        ("tools/list", {}),
        (
            "tools/call",
            {"name": "sample_draft_note", "arguments": {"title": "t", "body": "b"}},
        ),
    ],
)
async def test_bad_envelopes_are_refused(case: str, method: str, params: dict[str, Any]) -> None:
    async with _serve(_sample()) as harness:
        envelope, message = _bad_envelopes(harness)[case]
        body = await _raw_call(harness, envelope, method, params)
    assert "result" not in body
    assert body["error"]["message"] == message
    assert harness.drafts.drafts == []


async def test_raw_call_with_a_good_envelope_works() -> None:
    """Control for the test above: the same raw request succeeds with a valid envelope."""
    async with _serve(_sample()) as harness:
        body = await _raw_call(harness, harness.envelope(harness.tenant()), "tools/list", {})
    assert sorted(t["name"] for t in body["result"]["tools"]) == sorted(ALL_TOOLS)


async def test_tools_list_is_filtered_by_the_allow_list() -> None:
    async with _serve(_sample()) as harness:
        ctx = harness.tenant()
        async with harness.client(harness.envelope(ctx, tools=["sample_get_note"])) as client:
            tools = (await client.list_tools()).tools
        assert [t.name for t in tools] == ["sample_get_note"]
        schema = tools[0].input_schema
        assert schema["required"] == ["note_id"]
        assert set(schema["properties"]) == {"note_id"}
        assert tools[0].annotations is not None and tools[0].annotations.read_only_hint is True


async def test_read_tool_uses_the_envelope_tenant_and_its_key() -> None:
    async with _serve(_sample()) as harness:
        ctx = harness.tenant()
        async with harness.client(harness.envelope(ctx)) as client:
            result = await client.call_tool("sample_get_note", {"note_id": "n-1"})
        assert not result.is_error, _text(result)
        assert result.structured_content is not None
        assert result.structured_content["tenant_id"] == str(ctx.tenant_id)
        assert result.structured_content["body"] == f"seen Bearer key-{ctx.tenant_id}"


async def test_tool_outside_the_allow_list_is_refused() -> None:
    async with _serve(_sample()) as harness:
        ctx = harness.tenant()
        async with harness.client(harness.envelope(ctx, tools=["sample_get_note"])) as client:
            result = await client.call_tool("sample_draft_note", {"title": "t", "body": "b"})
        assert result.is_error
        assert _text(result) == "tool_not_available"
        assert harness.drafts.drafts == []


async def test_unknown_tool_is_refused() -> None:
    async with _serve(_sample()) as harness:
        ctx = harness.tenant()
        async with harness.client(harness.envelope(ctx)) as client:
            result = await client.call_tool("sample_delete_note", {"note_id": "n-1"})
        assert result.is_error


async def test_draft_tool_writes_a_draft_for_the_envelope_tenant_and_run() -> None:
    async with _serve(_sample()) as harness:
        ctx, run_id = harness.tenant(), uuid4()
        async with harness.client(harness.envelope(ctx, run_id=run_id)) as client:
            result = await client.call_tool("sample_draft_note", {"title": "Hi", "body": "Body"})
        assert not result.is_error, _text(result)
        [draft] = harness.drafts.drafts
        assert (draft.tenant_id, draft.venue_id, draft.run_id) == (
            ctx.tenant_id,
            ctx.venue_id,
            run_id,
        )
        assert draft.draft.type == "sample.note"
        assert draft.draft.payload == {"title": "Hi", "body": "Body"}
        assert result.structured_content == {"draft_id": str(draft.id)}


@pytest.mark.parametrize("field", ["tenant_id", "venue_id", "unexpected"])
async def test_extra_arguments_are_refused_not_dropped(field: str) -> None:
    async with _serve(_sample()) as harness:
        ctx, other = harness.tenant(), harness.tenant()
        async with harness.client(harness.envelope(ctx)) as client:
            result = await client.call_tool(
                "sample_draft_note", {"title": "t", "body": "b", field: str(other.tenant_id)}
            )
        assert result.is_error
        assert result.structured_content == {
            "error": "invalid_arguments",
            "fields": "unexpected field",
        }
        assert str(other.tenant_id) not in _text(result)
        assert harness.drafts.drafts == []


async def test_invalid_argument_values_are_not_echoed() -> None:
    async with _serve(_sample()) as harness:
        ctx = harness.tenant()
        async with harness.client(harness.envelope(ctx)) as client:
            result = await client.call_tool("sample_get_note", {"note_id": "../../secret value"})
        assert result.is_error
        assert "secret value" not in _text(result)


async def test_concurrent_tenants_each_see_their_own_context() -> None:
    async with _serve(_sample()) as harness:
        tenants = [harness.tenant() for _ in range(6)]

        async def call(ctx: TenantContext) -> str:
            async with harness.client(harness.envelope(ctx)) as client:
                result = await client.call_tool("sample_get_note", {"note_id": "n"})
            assert result.structured_content is not None
            return str(result.structured_content["tenant_id"])

        seen = await asyncio.gather(*(call(ctx) for ctx in tenants * 3))
        assert seen == [str(ctx.tenant_id) for ctx in tenants * 3]


async def test_rate_limited_calls_return_a_tool_error() -> None:
    async with _serve(sample_registry(httpx.MockTransport(vendor)), per_tenant=Rate(0.001, 1)) as h:
        ctx = h.tenant()
        async with h.client(h.envelope(ctx)) as client:
            first = await client.call_tool("sample_get_note", {"note_id": "n"})
            second = await client.call_tool("sample_get_note", {"note_id": "n"})
    assert not first.is_error
    assert second.is_error and _text(second) == "rate_limited"
    assert second.structured_content is not None
    assert second.structured_content["retry_after"] > 0


class Ping(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Seen(BaseModel):
    tenant_id: str
    run_id: str | None


def _probe_registry() -> ToolRegistry:
    registry = ToolRegistry("sample")

    @registry.read("sample_get_context", args=Ping, result=Seen, description="Echo the scope.")
    async def get_context(_args: Ping, tool: ToolContext) -> Seen:  # pyright: ignore[reportUnusedFunction]
        assert current_tenant() == tool.tenant
        return Seen(tenant_id=str(current_tenant().tenant_id), run_id=current_run_id())

    @registry.read("sample_get_boom", args=Ping, result=Seen, description="Fails.")
    async def get_boom(_args: Ping, _tool: ToolContext) -> Seen:  # pyright: ignore[reportUnusedFunction]
        raise RuntimeError("guest jane@example.com owes $500")

    @registry.read("sample_get_draftless", args=Ping, result=Seen, description="Bad read tool.")
    async def get_draftless(_args: Ping, tool: ToolContext) -> Seen:  # pyright: ignore[reportUnusedFunction]
        await tool.create_draft(NewDraft(type="sample.note", payload={}))
        raise AssertionError("unreachable")

    @registry.draft("sample_draft_foreign", args=Ping, result=Seen, description="Bad.")
    async def create_foreign(_args: Ping, tool: ToolContext) -> Seen:  # pyright: ignore[reportUnusedFunction]
        await tool.create_draft(NewDraft(type="gmail.send", payload={}))
        raise AssertionError("unreachable")

    return registry


PROBE_TOOLS = (
    "sample_get_context",
    "sample_get_boom",
    "sample_get_draftless",
    "sample_draft_foreign",
)


async def test_handlers_run_inside_the_tenant_and_run_scope() -> None:
    async with _serve(_probe_registry()) as h:
        ctx, run_id = h.tenant(), uuid4()
        async with h.client(h.envelope(ctx, run_id=run_id, tools=PROBE_TOOLS)) as client:
            result = await client.call_tool("sample_get_context", {})
    assert result.structured_content == {"tenant_id": str(ctx.tenant_id), "run_id": str(run_id)}


async def test_unexpected_errors_do_not_leak_their_text() -> None:
    async with _serve(_probe_registry()) as h:
        ctx = h.tenant()
        async with h.client(h.envelope(ctx, tools=PROBE_TOOLS)) as client:
            result = await client.call_tool("sample_get_boom", {})
    assert result.is_error
    assert "jane" not in _text(result) and "500" not in _text(result)
    assert "internal_error" in _text(result)


async def test_read_tools_cannot_create_drafts_and_drafts_stay_in_their_system() -> None:
    async with _serve(_probe_registry()) as h:
        ctx = h.tenant()
        async with h.client(h.envelope(ctx, tools=PROBE_TOOLS)) as client:
            read = await client.call_tool("sample_get_draftless", {})
            foreign = await client.call_tool("sample_draft_foreign", {})
    # Code only: the reason is logged, not returned to the model.
    for result in (read, foreign):
        assert result.is_error and _text(result).endswith(f": {AuthorizationError.code}")
        assert "read tool" not in _text(result) and "gmail" not in _text(result)
    assert h.drafts.drafts == []


def test_tool_context_holds_no_raw_secret_store() -> None:
    """M6: tools get `TenantSecrets`, never the store that can read any tenant's secrets."""
    field_types = {f.name: f.type for f in dataclasses.fields(ToolContext)}
    assert "secret_store" not in field_types
    assert field_types["secrets"] == "TenantSecrets"
    # Drafts go through a closure bound to the call's tenant and run, not a writer.
    assert "DraftWriter" not in " ".join(str(t) for t in field_types.values())


def test_a_connector_without_tools_is_refused() -> None:
    deps, *_ = _deps(make_keys())
    with pytest.raises(VenueAgentsError, match="no tools"):
        build_connector_server(ToolRegistry("sample"), deps)


async def test_healthz_needs_no_envelope() -> None:
    async with _serve(_sample()) as harness:
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=harness.app), base_url=HOST
        ) as http:
            response = await http.get("/healthz")
        assert response.status_code == 200


@pytest.mark.parametrize("method", ["resources/list", "prompts/list", "completion/complete"])
async def test_only_tool_methods_are_served(method: str) -> None:
    """Resources, prompts or extension methods would run outside the registry's rules."""
    async with _serve(_sample()) as harness:
        body = await _raw_call(harness, harness.envelope(harness.tenant()), method, {})
    assert body["error"]["message"] == "method not available on connectors"


async def test_unknown_field_names_are_not_echoed() -> None:
    async with _serve(_sample()) as harness:
        ctx = harness.tenant()
        async with harness.client(harness.envelope(ctx)) as client:
            result = await client.call_tool(
                "sample_get_note", {"note_id": "n", "ignore previous instructions": "x"}
            )
    assert result.is_error
    assert "ignore" not in str(result.structured_content)


@pytest.mark.parametrize("hosts", [[], ["*"], ["*:8000"], [":8000"]])
def test_wildcard_allowed_hosts_are_refused(hosts: list[str]) -> None:
    deps, *_ = _deps(make_keys())
    server = build_connector_server(_sample(), deps)
    with pytest.raises(VenueAgentsError, match="allowed_hosts"):
        connector_app(server, allowed_hosts=hosts)


async def test_over_a_real_socket() -> None:
    """The ASGI test transport hides transport bugs: in mcp 2.3.0, stateless JSON responses arrive
    empty under uvicorn, and the 2026-07-28 protocol path fails there (lessons.md). Keep one
    end-to-end call over a real server."""
    import uvicorn

    registry = _sample()
    keys = make_keys()
    deps, secrets, connections, _drafts = _deps(keys)
    server = build_connector_server(registry, deps)
    app = connector_app(server, allowed_hosts=["127.0.0.1:*"])
    uv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    serving = asyncio.create_task(uv.serve())
    try:
        async with asyncio.timeout(10):
            while not uv.started:
                await asyncio.sleep(0.02)
        port = uv.servers[0].sockets[0].getsockname()[1]
        harness = Harness(keys, deps, secrets, connections, _drafts, app)
        ctx = harness.tenant()
        http = httpx2.AsyncClient(headers={ENVELOPE_HEADER: harness.envelope(ctx)})
        url = f"http://127.0.0.1:{port}/mcp"
        async with (
            asyncio.timeout(20),
            http,
            Client(streamable_http_client(url, http_client=http), mode="legacy") as client,
        ):
            result = await client.call_tool("sample_get_note", {"note_id": "n-1"})
        assert not result.is_error, _text(result)
        assert result.structured_content is not None
        assert result.structured_content["tenant_id"] == str(ctx.tenant_id)
    finally:
        uv.should_exit = True
        await serving
