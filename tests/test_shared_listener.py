import pytest
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from scripts.serve_shared import PublicListener


async def origin(scope, receive, send):
    await PlainTextResponse("origin reached")(scope, receive, send)


@pytest.mark.parametrize("path", ["/admin/login", "/admin/api/export", "/docs", "/openapi.json"])
def test_public_listener_blocks_private_http_routes(path):
    response = TestClient(PublicListener(origin)).get(path)
    assert response.status_code == 404
    assert "origin reached" not in response.text


@pytest.mark.parametrize(
    "path", ["/client", "/health", "/api/v1/me", "/l/test", "/static/client.js"]
)
def test_public_listener_forwards_client_routes(path):
    response = TestClient(PublicListener(origin)).get(path)
    assert response.status_code == 200
    assert response.text == "origin reached"


def test_public_listener_rejects_private_websocket_path():
    with pytest.raises(WebSocketDisconnect) as error:
        with TestClient(PublicListener(origin)).websocket_connect("/admin"):
            pytest.fail("Private WebSocket path was accepted")
    assert error.value.code == 1008
