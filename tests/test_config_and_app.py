from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.main import app


def test_regions_from_comma_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAVEN_REGIONS", "US, eu")
    assert Settings().regions == ["us", "eu"]


def test_unknown_region_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAVEN_REGIONS", "us,mars")
    with pytest.raises(ValidationError):
        Settings()


def test_defaults_bind_to_localhost() -> None:
    assert Settings().host == "127.0.0.1"


def test_health() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_version_matches_pyproject() -> None:
    import tomllib
    from pathlib import Path

    from app import __version__

    pyproject = tomllib.loads((Path(__file__).parent.parent / "pyproject.toml").read_text())
    assert pyproject["project"]["version"] == __version__
