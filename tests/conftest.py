from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_memographix_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMOGRAPHIX_HOME", str(tmp_path / "memographix-home"))
    monkeypatch.setenv("MEMOGRAPHIX_CODEX_CONFIG", str(tmp_path / "codex-config.toml"))
    monkeypatch.setenv("MEMOGRAPHIX_CODEX_SKILLS_DIR", str(tmp_path / "codex-skills"))
