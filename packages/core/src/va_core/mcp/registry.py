"""Tool registry: connector tools may only read or draft (ADR-0005).

Registration fails at start-up (`ToolRegistrationError`) for:
- any category other than `read` or `draft` (there is deliberately no `write`);
- a name that isn't `<system>_<verb>_<noun>` (Claude tool names allow only `[a-zA-Z0-9_-]{1,64}`);
- a duplicate name;
- an arguments model that accepts unknown fields or has a tenant field (`tenant_id`, `venue_id`,
  `stamp_id`, `run_id`): the tenant comes from the run envelope, never from tool arguments.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from pydantic import BaseModel

from va_core.errors import ToolRegistrationError
from va_core.tools import ToolCategory

if TYPE_CHECKING:
    from va_core.mcp.context import ToolContext

MAX_NAME_LENGTH = 64
__all__ = ["ToolCategory", "ToolHandler", "ToolRegistry", "ToolSpec"]

RESERVED_ARGUMENTS = frozenset({"tenant_id", "venue_id", "stamp_id", "run_id"})
_SYSTEM = re.compile(r"^[a-z][a-z0-9]{0,31}$")
_NAME_TAIL = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)+$")


type ToolHandler[A: BaseModel, R: BaseModel] = Callable[[A, ToolContext], Awaitable[R]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    category: ToolCategory
    description: str
    args_model: type[BaseModel]
    result_model: type[BaseModel]
    handler: ToolHandler[BaseModel, BaseModel]


class ToolRegistry:
    """The tools one connector system exposes."""

    def __init__(self, system: str) -> None:
        if not _SYSTEM.fullmatch(system):
            raise ToolRegistrationError(
                f"invalid connector system {system!r}: lowercase letters and digits"
            )
        self.system = system
        self._tools: dict[str, ToolSpec] = {}

    def register[A: BaseModel, R: BaseModel](
        self,
        name: str,
        *,
        category: ToolCategory | str,
        args: type[A],
        result: type[R],
        description: str,
        handler: ToolHandler[A, R],
    ) -> ToolSpec:
        try:
            checked = ToolCategory(category)
        except ValueError:
            raise ToolRegistrationError(
                f"tool {name!r} has category {category!r}: connector tools may only read or draft"
            ) from None
        self._check_name(name)
        self._check_args(name, args)
        if not description.strip():
            raise ToolRegistrationError(f"tool {name!r} needs a description")
        spec = ToolSpec(
            name=name,
            category=checked,
            description=description,
            args_model=args,
            result_model=result,
            handler=cast(ToolHandler[BaseModel, BaseModel], handler),
        )
        self._tools[name] = spec
        return spec

    def read[A: BaseModel, R: BaseModel](
        self, name: str, *, args: type[A], result: type[R], description: str
    ) -> Callable[[ToolHandler[A, R]], ToolHandler[A, R]]:
        """Decorator for a read tool."""
        return self._decorator(name, ToolCategory.READ, args, result, description)

    def draft[A: BaseModel, R: BaseModel](
        self, name: str, *, args: type[A], result: type[R], description: str
    ) -> Callable[[ToolHandler[A, R]], ToolHandler[A, R]]:
        """Decorator for a draft tool: it may only write drafts (`ToolContext.create_draft`)."""
        return self._decorator(name, ToolCategory.DRAFT, args, result, description)

    def _decorator[A: BaseModel, R: BaseModel](
        self,
        name: str,
        category: ToolCategory,
        args: type[A],
        result: type[R],
        description: str,
    ) -> Callable[[ToolHandler[A, R]], ToolHandler[A, R]]:
        def decorate(handler: ToolHandler[A, R]) -> ToolHandler[A, R]:
            self.register(
                name,
                category=category,
                args=args,
                result=result,
                description=description,
                handler=handler,
            )
            return handler

        return decorate

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def __iter__(self) -> Iterator[ToolSpec]:
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)

    def _check_name(self, name: str) -> None:
        prefix = f"{self.system}_"
        if (
            len(name) > MAX_NAME_LENGTH
            or not name.startswith(prefix)
            or not _NAME_TAIL.fullmatch(name.removeprefix(prefix))
        ):
            raise ToolRegistrationError(
                f"tool name {name!r} must be {prefix}<verb>_<noun> (lowercase, at most "
                f"{MAX_NAME_LENGTH} characters)"
            )
        if name in self._tools:
            raise ToolRegistrationError(f"tool {name!r} is already registered")

    @staticmethod
    def _check_args(name: str, args: type[BaseModel]) -> None:
        if args.model_config.get("extra") != "forbid":
            raise ToolRegistrationError(f"tool {name!r}: arguments model must set extra='forbid'")
        aliased = [
            name
            for name, info in args.model_fields.items()
            if info.alias or info.validation_alias or info.serialization_alias
        ]
        if aliased:
            # Aliases would let a reserved name in by another spelling (and break the schema).
            raise ToolRegistrationError(f"tool {name!r}: arguments may not use aliases {aliased}")
        reserved = RESERVED_ARGUMENTS & set(args.model_fields)
        if reserved:
            raise ToolRegistrationError(
                f"tool {name!r}: arguments may not include {sorted(reserved)}; "
                "the tenant comes from the run envelope"
            )
