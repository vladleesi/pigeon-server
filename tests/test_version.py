"""The health probe and API schema identify the same backend release."""

import re

from fastapi.testclient import TestClient

from app.main import create_app
from app.version import __version__


def test_release_version_is_shared_by_health_and_openapi():
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)
    client = TestClient(create_app())
    assert client.get("/health").json() == {"status": "ok", "version": __version__}
    info = client.get("/openapi.json").json()["info"]
    assert info["version"] == __version__
    assert info["title"] == "Sideword Server"
