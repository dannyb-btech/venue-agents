"""Guard: configuration is read only through pydantic-settings classes (python.md, P0-02)."""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ALLOWED = {"settings.py"}
PATTERN = re.compile(r"\bos\.(environ|getenv|putenv)\b|\bfrom os import [^\n]*\b(environ|getenv)\b")


def test_no_environment_reads_outside_settings() -> None:
    offenders: list[str] = []
    for top in ("apps", "packages"):
        for path in (REPO_ROOT / top).rglob("*.py"):
            parts = set(path.parts)
            if "tests" in parts or "node_modules" in parts or path.name in ALLOWED:
                continue
            if PATTERN.search(path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == [], f"read config via a settings class instead: {offenders}"
