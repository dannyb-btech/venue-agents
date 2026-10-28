import asyncio
from uuid import uuid4

import pytest
from pydantic import ValidationError

from va_core import (
    Role,
    TenantContext,
    TenantContextError,
    TenantIdentity,
    current_run_id,
    current_tenant,
    current_tenant_or_none,
    run_scope,
    tenant_scope,
)


def make_ctx(**overrides: object) -> TenantContext:
    data: dict[str, object] = {
        "tenant_id": uuid4(),
        "venue_id": uuid4(),
        "stamp_id": "canary-local",
        "user_id": "user-1",
        "roles": frozenset({Role.MANAGER}),
    }
    data.update(overrides)
    return TenantContext.model_validate(data)


def test_current_tenant_fails_closed_outside_scope() -> None:
    assert current_tenant_or_none() is None
    with pytest.raises(TenantContextError):
        current_tenant()


def test_scope_sets_and_resets() -> None:
    ctx = make_ctx()
    with tenant_scope(ctx) as active:
        assert active is ctx
        assert current_tenant() is ctx
    assert current_tenant_or_none() is None


def test_scope_resets_after_exception() -> None:
    with pytest.raises(RuntimeError), tenant_scope(make_ctx()):
        raise RuntimeError("boom")
    assert current_tenant_or_none() is None


def test_switching_tenant_inside_scope_raises() -> None:
    with tenant_scope(make_ctx()), pytest.raises(TenantContextError), tenant_scope(make_ctx()):
        pass


def test_switching_venue_inside_scope_raises() -> None:
    ctx = make_ctx()
    other_venue = make_ctx(tenant_id=ctx.tenant_id)
    with tenant_scope(ctx), pytest.raises(TenantContextError), tenant_scope(other_venue):
        pass


def test_changing_user_or_roles_inside_scope_raises() -> None:
    viewer = make_ctx(roles=frozenset({Role.VIEWER}))
    owner = viewer.model_copy(update={"user_id": "someone-else", "roles": frozenset({Role.OWNER})})
    with tenant_scope(viewer), pytest.raises(TenantContextError), tenant_scope(owner):
        pass


def test_reentering_same_tenant_is_allowed() -> None:
    ctx = make_ctx()
    with tenant_scope(ctx), tenant_scope(ctx):
        assert current_tenant() is ctx
    assert current_tenant_or_none() is None


async def test_propagates_across_await_gather_and_tasks() -> None:
    ctx = make_ctx()

    async def read() -> TenantContext:
        await asyncio.sleep(0)
        return current_tenant()

    with tenant_scope(ctx):
        assert await read() is ctx
        results = await asyncio.gather(read(), read())
        assert all(r is ctx for r in results)
        assert await asyncio.create_task(read()) is ctx


async def test_concurrent_tasks_keep_their_own_tenant() -> None:
    contexts = [make_ctx() for _ in range(20)]

    async def worker(ctx: TenantContext) -> tuple[TenantContext, TenantContext]:
        with tenant_scope(ctx):
            await asyncio.sleep(0)
            seen = current_tenant()
            await asyncio.sleep(0)
            return ctx, seen

    for expected, seen in await asyncio.gather(*(worker(c) for c in contexts)):
        assert seen is expected
    assert current_tenant_or_none() is None


async def test_task_scope_does_not_leak_to_parent() -> None:
    async def child() -> None:
        with tenant_scope(make_ctx()):
            await asyncio.sleep(0)

    await asyncio.create_task(child())
    assert current_tenant_or_none() is None


def test_run_scope() -> None:
    assert current_run_id() is None
    with run_scope("run-1"):
        assert current_run_id() == "run-1"
    assert current_run_id() is None
    for bad in ("", "run\nFAKE log line", "x" * 129):
        with pytest.raises(ValueError), run_scope(bad):
            pass


def test_context_is_immutable_and_strict() -> None:
    ctx = make_ctx()
    with pytest.raises(ValidationError):
        ctx.tenant_id = uuid4()  # type: ignore[misc]
    with pytest.raises(ValidationError):
        make_ctx(unexpected="x")
    with pytest.raises(ValidationError):
        make_ctx(stamp_id="")


def test_has_role() -> None:
    ctx = make_ctx(roles=frozenset({Role.APPROVER}))
    assert ctx.has_role(Role.APPROVER, Role.OWNER)
    assert not ctx.has_role(Role.OWNER)


def test_tenant_identity_is_frozen_and_strict() -> None:
    identity = TenantIdentity(tenant_id=uuid4(), stamp_id="canary-local", user_id="oid-1")
    with pytest.raises(ValidationError):
        identity.tenant_id = uuid4()  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises(ValidationError):
        TenantIdentity(tenant_id=uuid4(), stamp_id="canary-local", venue_id=uuid4())  # pyright: ignore[reportCallIssue]
