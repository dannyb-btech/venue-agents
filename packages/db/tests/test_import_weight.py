"""Services that don't serve MCP must not load the connector framework's dependencies."""

from __future__ import annotations

import subprocess
import sys

CHECK = """
import sys
import va_core.credentials, va_core.tools, va_db.connectors, va_db.models, va_db.session
loaded = sorted(m for m in ("mcp", "jwt", "httpx2", "starlette") if m in sys.modules)
print(",".join(loaded))
"""


def test_db_and_shared_types_import_without_mcp() -> None:
    # A fresh interpreter: this test process has already imported mcp for other tests.
    result = subprocess.run(  # noqa: S603 (fixed code, this interpreter)
        [sys.executable, "-c", CHECK], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == ""
