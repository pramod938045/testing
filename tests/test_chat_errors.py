"""The chat endpoint must always answer with JSON.

The page parses every response as JSON, so a plain-text 500 shows up as
"Unexpected token 'I', "Internal S"... is not valid JSON" instead of the
real problem. These tests pin that shut.
"""

import json
from pathlib import Path

import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main


@pytest.fixture
def client(monkeypatch):
    """A test client whose Jira is stubbed and whose agent is controllable."""
    monkeypatch.setitem(main.state, "jira", object())
    monkeypatch.setitem(main.state, "jira_user", {"display_name": "Pramod"})
    monkeypatch.setattr(main.settings, "anthropic_api_key", "sk-ant-api03-" + "x" * 90)
    return TestClient(main.app, raise_server_exceptions=False)


def agent_raising(monkeypatch, error):
    class Agent:
        async def chat(self, message):
            raise error

    monkeypatch.setattr(main.sessions, "_factory", lambda: Agent())
    monkeypatch.setattr(main.sessions, "_items", {})


def api_error(cls, status, message):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status, request=request, json={"error": {"message": message}})
    return cls(message, response=response, body=None)


def test_a_missing_key_gives_a_readable_json_message(client, monkeypatch):
    """Without a key the SDK raises TypeError mid-request — a plain-text 500."""
    monkeypatch.setattr(main.settings, "anthropic_api_key", "")

    response = client.post("/api/chat", json={"message": "hi"})

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/json")
    detail = response.json()["detail"]
    assert "app.setup" in detail
    assert "/lookup" in detail


def test_an_account_with_no_credit_is_named(client, monkeypatch):
    agent_raising(
        monkeypatch,
        api_error(anthropic.BadRequestError, 400, "Your credit balance is too low"),
    )
    response = client.post("/api/chat", json={"message": "hi"})

    assert response.status_code == 402
    assert "no credit" in response.json()["detail"]
    assert "billing" in response.json()["detail"]


def test_an_unexpected_error_still_returns_json(client, monkeypatch):
    """The exact bug: TypeError from the SDK escaped as text/plain."""
    agent_raising(monkeypatch, TypeError("Could not resolve authentication method."))

    response = client.post("/api/chat", json={"message": "hi"})

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    json.loads(response.text)  # must parse — this is what the page does
    assert "went wrong" in response.json()["detail"]


@pytest.mark.parametrize(
    "error,status",
    [
        (api_error(anthropic.AuthenticationError, 401, "bad key"), 502),
        (api_error(anthropic.RateLimitError, 429, "slow down"), 429),
        (api_error(anthropic.InternalServerError, 500, "boom"), 502),
        (anthropic.APIConnectionError(request=httpx.Request("POST", "https://x")), 502),
        (ValueError("something odd"), 500),
    ],
)
def test_every_failure_is_json_with_a_detail(client, monkeypatch, error, status):
    agent_raising(monkeypatch, error)
    response = client.post("/api/chat", json={"message": "hi"})

    assert response.status_code == status
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["detail"]


def test_both_pages_survive_a_non_json_response():
    """A crash or proxy can still return HTML; the pages must show it readably."""
    for name in ("index.html", "lookup.html"):
        page = Path("app/static") / name
        source = page.read_text(encoding="utf-8")
        assert "JSON.parse(text)" in source, f"{name} parses the body defensively"
        assert "await res.json()" not in source, f"{name} must not call res.json() directly"
