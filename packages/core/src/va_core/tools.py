"""Tool categories shared by the connector framework, the agent runtime and `tool_call` rows."""

from __future__ import annotations

from enum import StrEnum


class ToolCategory(StrEnum):
    """Agent tools may only read or draft (ADR-0005). There is deliberately no `write`."""

    READ = "read"
    DRAFT = "draft"
