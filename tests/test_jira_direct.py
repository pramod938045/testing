"""Real Jira mode: live issues, no AI, no sample data."""

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.jira_client import JiraClient
from app.jira_direct import JiraDirectAgent, find_keys, render_issue

REAL_ISSUE = {
    "key": "UPAMCORE-30728",
    "fields": {
        "summary": "Player wallet balance mismatch after failed withdrawal",
        "issuetype": {"name": "Bug"},
        "status": {"name": "In Progress", "statusCategory": {"name": "In Progress"}},
        "priority": {"name": "Critical"},
        "project": {"name": "UPAM Core", "key": "UPAMCORE"},
        "assignee": {"displayName": "Ana Silva"},
        "reporter": {"displayName": "Rayanagoudra, Pramod"},
        "created": "2026-08-20T09:00:00.000+0000",
        "updated": "2026-09-04T16:20:00.000+0000",
        "labels": ["wallet", "urgent"],
        "description": {
            "type": "doc",
            "content": [
                {"type": "paragraph",
                 "content": [{"type": "text", "text": "Balance is not restored when a "
                                                      "withdrawal fails at the provider."}]}
            ],
        },
        "issuelinks": [
            {
                "type": {"outward": "blocks", "inward": "is blocked by"},
                "outwardIssue": {
                    "key": "UPAMCORE-30800",
                    "fields": {"summary": "Wallet reconciliation job",
                               "status": {"name": "To Do"}},
                },
            }
        ],
        "subtasks": [],
        "comment": {"comments": [
            {"author": {"displayName": "Ana Silva"}, "created": "2026-09-01T10:00:00.000+0000",
             "body": {"type": "doc", "content": [
                 {"type": "paragraph", "content": [{"type": "text", "text": "Reproduced in UAT."}]}]}}
        ]},
    },
}


def agent_for(handler) -> JiraDirectAgent:
    jira = JiraClient("https://scientificgames.atlassian.net", "me@example.com", "t")
    jira._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://scientificgames.atlassian.net"
    )
    return JiraDirectAgent(jira=jira)


def ok(request):
    return httpx.Response(200, json=REAL_ISSUE)


def fail(status, message):
    def handler(request):
        return httpx.Response(status, json={"errorMessages": [message]})

    return handler


# --- finding the key in a sentence -----------------------------------------


@pytest.mark.parametrize("message,expected", [
    ("UPAMCORE-30728", ["UPAMCORE-30728"]),
    ("what does UPAMCORE-30728 say?", ["UPAMCORE-30728"]),
    ("upamcore-30728", ["UPAMCORE-30728"]),
    ("compare DFE-9067 and UPAM-3395", ["DFE-9067", "UPAM-3395"]),
    ("UPAMCORE-30728 again UPAMCORE-30728", ["UPAMCORE-30728"]),
    ("what's assigned to me", []),
])
def test_issue_keys_are_found_in_free_text(message, expected):
    assert find_keys(message) == expected


# --- fetching the real thing ------------------------------------------------


async def test_a_real_key_fetches_that_issue_from_jira():
    agent = agent_for(ok)
    result = await agent.chat("UPAMCORE-30728")

    assert "UPAMCORE-30728" in result.reply
    assert "Player wallet balance mismatch" in result.reply
    assert "In Progress" in result.reply
    assert "Ana Silva" in result.reply
    assert "Balance is not restored" in result.reply
    assert "UPAMCORE-30800" in result.reply, "linked issues should be shown"
    assert result.tool_calls[0].name == "get_issue"
    assert result.tool_calls[0].ok is True
    await agent.jira.aclose()


async def test_no_sample_data_can_appear_in_real_mode():
    agent = agent_for(ok)
    for message in ("UPAMCORE-30728", "what's the status?", "hello"):
        reply = (await agent.chat(message)).reply
        assert "DEMO-1" not in reply
        assert "DEMO-7" not in reply
        assert "Self-service account closure" not in reply
    await agent.jira.aclose()


async def test_the_request_is_a_read():
    seen = []

    def handler(request):
        seen.append(request.method)
        return httpx.Response(200, json=REAL_ISSUE)

    agent = agent_for(handler)
    await agent.chat("UPAMCORE-30728")

    assert seen == ["GET"]
    await agent.jira.aclose()


# --- follow-up questions use the retrieved data ----------------------------


@pytest.mark.parametrize("question,expected", [
    ("what's the status?", "In Progress"),
    ("who is it assigned to?", "Ana Silva"),
    ("who reported it?", "Rayanagoudra, Pramod"),
    ("what priority?", "Critical"),
    ("show the description", "Balance is not restored"),
    ("any linked issues?", "UPAMCORE-30800"),
    ("show the comments", "Reproduced in UAT"),
    ("what labels does it have?", "wallet"),
])
async def test_follow_ups_are_answered_from_the_fetched_issue(question, expected):
    agent = agent_for(ok)
    await agent.chat("UPAMCORE-30728")

    reply = (await agent.chat(question)).reply
    assert expected in reply
    assert "UPAMCORE-30728" in reply
    await agent.jira.aclose()


async def test_a_follow_up_makes_no_second_request():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json=REAL_ISSUE)

    agent = agent_for(handler)
    await agent.chat("UPAMCORE-30728")
    await agent.chat("what's the status?")

    assert len(calls) == 1, "the follow-up should use the data already retrieved"
    await agent.jira.aclose()


# --- errors -----------------------------------------------------------------


async def test_a_missing_issue_says_so_clearly():
    agent = agent_for(fail(404, "Issue does not exist or you do not have permission to see it."))
    result = await agent.chat("UPAMCORE-99999")

    assert "not found" in result.reply.lower()
    assert "UPAMCORE-99999" in result.reply
    assert result.tool_calls[0].ok is False
    await agent.jira.aclose()


@pytest.mark.parametrize("status", [401, 403])
async def test_an_auth_failure_shows_the_real_jira_error(status):
    agent = agent_for(fail(status, "Client must be authenticated to access this resource."))
    result = await agent.chat("UPAMCORE-30728")

    assert "Client must be authenticated" in result.reply, "show Jira's own words"
    assert "refused" in result.reply.lower()
    await agent.jira.aclose()


async def test_an_unknown_question_with_nothing_fetched_explains_what_to_type():
    agent = agent_for(ok)
    result = await agent.chat("hello there")

    assert "issue key" in result.reply.lower()
    assert result.tool_calls == []
    await agent.jira.aclose()


def test_rendering_copes_with_a_sparse_issue():
    text = render_issue({"key": "X-1", "summary": "Bare", "url": "https://x/browse/X-1"})

    assert "X-1" in text and "Bare" in text
    assert "_(empty)_" in text


# --- the endpoint -----------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    jira = JiraClient("https://scientificgames.atlassian.net", "me@example.com", "t")
    jira._client = httpx.AsyncClient(
        transport=httpx.MockTransport(ok), base_url="https://scientificgames.atlassian.net"
    )
    monkeypatch.setitem(main.state, "jira", jira)
    monkeypatch.setattr(main.jira_sessions, "_items", {})
    return TestClient(main.app, raise_server_exceptions=False)


def test_jira_mode_returns_the_real_issue(client, monkeypatch):
    monkeypatch.setattr(main.settings, "anthropic_api_key", "sk-ant-" + "x" * 90)

    response = client.post("/api/chat", json={"message": "UPAMCORE-30728", "mode": "jira"})

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "jira"
    assert "Player wallet balance mismatch" in body["reply"]


def test_jira_mode_is_used_when_there_is_no_ai_key(client, monkeypatch):
    monkeypatch.setattr(main.settings, "anthropic_api_key", "")

    body = client.post("/api/chat", json={"message": "UPAMCORE-30728"}).json()

    assert body["mode"] == "jira"
    assert "UPAMCORE-30728" in body["reply"]


def test_demo_mode_still_wins_when_asked_for(client, monkeypatch):
    monkeypatch.setattr(main.settings, "anthropic_api_key", "")
    monkeypatch.setattr(main.demo_sessions, "_items", {})

    body = client.post("/api/chat", json={"message": "UPAMCORE-30728", "demo": True}).json()

    assert body["mode"] == "demo"
    assert "UPAMCORE" not in body["reply"], "demo mode must not reach real Jira"


def test_the_three_modes_keep_separate_conversations(client, monkeypatch):
    monkeypatch.setattr(main.settings, "anthropic_api_key", "")
    monkeypatch.setattr(main.demo_sessions, "_items", {})

    client.post("/api/chat", json={"message": "hi", "session_id": "s1", "mode": "jira"})
    client.post("/api/chat", json={"message": "hi", "session_id": "s1", "demo": True})

    assert "s1" in main.jira_sessions
    assert "s1" in main.demo_sessions
    assert "s1" not in main.sessions


def test_the_page_offers_real_jira_mode():
    from pathlib import Path

    page = Path("app/static/index.html").read_text(encoding="utf-8")
    assert "params.has('jira')" in page
    assert "jiraBar" in page
    assert "mode: chatMode" in page
