"""The published OpenAPI contract (P0-05 acceptance: no endpoint accepts tenant_id)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from va_platform_api.__main__ import openapi_document
from va_platform_api.api import VENUE_HEADER

COMMITTED = Path(__file__).parents[1] / "openapi.json"


def test_no_request_input_names_a_tenant() -> None:
    doc = cast(dict[str, Any], openapi_document())
    inputs: list[str] = []
    for path_item in doc["paths"].values():
        for operation in path_item.values():
            inputs += [p["name"] for p in operation.get("parameters", [])]
    request_models = [name for name in doc["components"]["schemas"] if name.endswith("In")]
    for name in request_models:
        inputs += list(doc["components"]["schemas"][name].get("properties", {}))
    assert not [i for i in inputs if "tenant" in i.lower()]
    # The venue is only ever the authorised selector header (ADR-0019).
    assert [i for i in inputs if "venue" in i.lower()] == [VENUE_HEADER] * inputs.count(
        VENUE_HEADER
    )
    assert VENUE_HEADER in inputs


def test_committed_openapi_is_current() -> None:
    current = json.dumps(openapi_document(), indent=2, sort_keys=True) + "\n"
    assert COMMITTED.read_text(encoding="utf-8") == current, (
        "regenerate: uv run python -m va_platform_api openapi > apps/platform-api/openapi.json"
    )


def test_every_venue_endpoint_requires_auth() -> None:
    doc = cast(dict[str, Any], openapi_document())
    for path, path_item in doc["paths"].items():
        for method, operation in path_item.items():
            assert operation.get("security"), f"{method} {path} has no security requirement"
