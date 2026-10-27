"""Tool registry: read and draft tools only; names and arguments follow the rules."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from va_core.errors import ToolRegistrationError
from va_core.mcp.context import ToolContext
from va_core.mcp.registry import ToolCategory, ToolRegistry


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: str


class Out(BaseModel):
    ok: bool


async def handler(args: Args, tool: ToolContext) -> Out:
    return Out(ok=True)


def _register(registry: ToolRegistry, name: str = "sample_get_note", **kw: object) -> None:
    kwargs: dict[str, object] = {
        "category": "read",
        "args": Args,
        "result": Out,
        "description": "d",
        "handler": handler,
    }
    kwargs.update(kw)
    registry.register(name, **kwargs)  # type: ignore[arg-type]


def test_read_and_draft_register() -> None:
    registry = ToolRegistry("sample")
    _register(registry, "sample_get_note", category=ToolCategory.READ)
    _register(registry, "sample_draft_note", category="draft")
    assert {spec.name: spec.category for spec in registry} == {
        "sample_get_note": ToolCategory.READ,
        "sample_draft_note": ToolCategory.DRAFT,
    }


def test_write_tool_is_rejected_at_registration() -> None:
    registry = ToolRegistry("sample")
    with pytest.raises(ToolRegistrationError, match="may only read or draft"):
        _register(registry, "sample_send_note", category="write")
    assert len(registry) == 0


@pytest.mark.parametrize("category", ["WRITE", "execute", "", "read ", "admin"])
def test_any_other_category_is_rejected(category: str) -> None:
    with pytest.raises(ToolRegistrationError):
        _register(ToolRegistry("sample"), category=category)


@pytest.mark.parametrize(
    "name",
    [
        "gmail_search_threads",  # another system's prefix
        "sample.get_note",  # dotted names are not valid Claude tool names
        "sample_getnote",  # needs <verb>_<noun>
        "sample_Get_note",
        "sample_get-note",
        "sample_",
        "sample_" + "a_" * 30 + "x",  # over 64 characters
    ],
)
def test_bad_names_are_rejected(name: str) -> None:
    with pytest.raises(ToolRegistrationError, match="tool name"):
        _register(ToolRegistry("sample"), name)


def test_duplicate_is_rejected() -> None:
    registry = ToolRegistry("sample")
    _register(registry)
    with pytest.raises(ToolRegistrationError, match="already registered"):
        _register(registry)


def test_args_must_forbid_extra_fields() -> None:
    class Loose(BaseModel):
        q: str

    with pytest.raises(ToolRegistrationError, match="extra='forbid'"):
        _register(ToolRegistry("sample"), args=Loose)


@pytest.mark.parametrize("field", ["tenant_id", "venue_id", "stamp_id", "run_id"])
def test_args_may_not_name_the_tenant(field: str) -> None:
    model = type(
        "Bad",
        (BaseModel,),
        {"__annotations__": {field: str}, "model_config": ConfigDict(extra="forbid")},
    )
    with pytest.raises(ToolRegistrationError, match="run envelope"):
        _register(ToolRegistry("sample"), args=model)


@pytest.mark.parametrize(
    "field",
    [
        Field(alias="tenant_id"),
        Field(validation_alias="tenant_id"),
        Field(validation_alias=AliasChoices("q", "tenant_id")),
        Field(serialization_alias="other"),
    ],
)
def test_args_may_not_use_aliases(field: Any) -> None:
    """An alias could let `tenant_id` in by another spelling."""
    model = type(
        "Aliased",
        (BaseModel,),
        {
            "__annotations__": {"tenant": str},
            "tenant": field,
            "model_config": ConfigDict(extra="forbid"),
        },
    )
    with pytest.raises(ToolRegistrationError, match="aliases"):
        _register(ToolRegistry("sample"), args=model)


def test_description_required() -> None:
    with pytest.raises(ToolRegistrationError, match="description"):
        _register(ToolRegistry("sample"), description="  ")


def test_bad_system() -> None:
    with pytest.raises(ToolRegistrationError):
        ToolRegistry("Sample_X")
