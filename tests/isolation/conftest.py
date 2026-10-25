"""Every test under tests/isolation is a cross-tenant isolation test."""

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "tests/isolation" in item.path.as_posix():
            item.add_marker(pytest.mark.isolation)
