from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_from_repo_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Settings read `.env` from the working directory; keep the developer's file out of tests."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENVIRONMENT", "test")
