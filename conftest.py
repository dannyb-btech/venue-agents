"""Repo-wide pytest hooks (rootdir conftest: applies to apps, packages and tests)."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable

import pytest


def pytest_asyncio_loop_factories(
    config: pytest.Config, item: pytest.Item
) -> dict[str, Callable[[], asyncio.AbstractEventLoop]]:
    # psycopg's async driver can't use Windows' default Proactor loop.
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.new_event_loop}
