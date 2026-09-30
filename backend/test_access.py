"""Who can reach the backend.

It listens on 127.0.0.1, which keeps other machines out but not other pages:
a site the tester has open can point a name of its own at this machine (DNS
rebinding), and to the browser that page is then the same origin as the
backend, so CORS never stops it. A socket is not covered by CORS at all.
"""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import main


@pytest.fixture
def client():
    # Named as the frontend names it. No `with`: the app's startup drives
    # real browsers and processes, and none of that is under test here.
    return TestClient(main.app, base_url="http://localhost")


def test_a_request_named_for_this_machine_is_served(client):
    assert client.get("/api/test-data").status_code == 200


def test_a_request_named_for_another_host_is_refused(client):
    """What a rebinding page's requests carry: its own name."""
    assert client.get("/api/test-data", headers={"host": "evil.example"}).status_code == 400


def test_a_socket_opened_by_another_site_is_closed(client):
    # Named for this machine, so it reaches the origin check. Without the name
    # the test client's socket was turned away by the host check first, and
    # this test passed or failed on that instead — the origin check untested.
    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect("/ws/session/nope/screen",
                                      headers={"origin": "https://evil.example",
                                               "host": "localhost"}):
            pass
    assert refused.value.code == 1008


def test_a_socket_named_for_another_host_is_refused(client):
    """What a rebinding page's socket carries: its own name, and no Origin
    the check could hold against it."""
    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect("/ws/session/nope/screen",
                                      headers={"host": "evil.example"}):
            pass
    assert getattr(refused.value, "status_code", None) == 400
