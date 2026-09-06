import json
from typing import Any, Callable

import httpx
import pytest

from app.jira_client import JiraClient

BASE_URL = "https://example.atlassian.net"


@pytest.fixture
def make_client() -> Callable[..., tuple[JiraClient, list[httpx.Request]]]:
    """Build a JiraClient wired to a mock transport.

    `routes` maps "METHOD /path" to either a dict (200 + JSON) or an
    (status_code, payload) tuple. Returns the client and a list that records
    every request made, so tests can assert on the payload sent to Jira.
    """

    def _make(routes: dict[str, Any]) -> tuple[JiraClient, list[httpx.Request]]:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            key = f"{request.method} {request.url.path}"
            if key not in routes:
                return httpx.Response(404, json={"errorMessages": [f"no route for {key}"]})
            entry = routes[key]
            status, payload = entry if isinstance(entry, tuple) else (200, entry)
            if payload is None:
                return httpx.Response(status)
            return httpx.Response(status, content=json.dumps(payload), headers={"content-type": "application/json"})

        transport = httpx.MockTransport(handler)
        http_client = httpx.AsyncClient(transport=transport, base_url=BASE_URL)
        return JiraClient(BASE_URL, "me@example.com", "token", client=http_client), seen

    return _make
