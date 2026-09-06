"""Demo mode in the web chat: sample answers, no Jira and no AI call."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.demo_agent import DemoAgent, answer


@pytest.fixture
def client(monkeypatch):
    """No API key configured — the situation demo mode exists for."""
    monkeypatch.setitem(main.state, "jira", object())
    monkeypatch.setattr(main.settings, "anthropic_api_key", "")
    monkeypatch.setattr(main.demo_sessions, "_items", {})
    return TestClient(main.app, raise_server_exceptions=False)


def ask(client, message, session_id=None):
    response = client.post(
        "/api/chat", json={"message": message, "demo": True, "session_id": session_id}
    )
    assert response.status_code == 200, response.text
    return response.json()


# --- the guarantee ---------------------------------------------------------


def test_demo_chat_answers_with_no_api_key(client):
    data = ask(client, "What does DEMO-1 say?")

    assert "DEMO-1" in data["reply"]
    assert "Self-service account closure" in data["reply"]


def test_demo_chat_never_calls_jira_or_the_ai(client, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("demo mode must not use the real agent")

    monkeypatch.setattr(main.sessions, "_factory", boom)
    ask(client, "What's assigned to me and not done?")


def test_without_the_demo_flag_a_missing_key_is_still_reported(client):
    """Demo mode is opt-in — it must not silently replace real answers."""
    response = client.post("/api/chat", json={"message": "hi"})

    assert response.status_code == 503
    assert "/?demo" in response.json()["detail"]


def test_every_demo_reply_says_it_is_sample_data(client):
    for question in ("What does DEMO-7 say?", "Summarise the current sprint", "hello"):
        assert "not your Jira" in ask(client, question)["reply"]


def test_demo_and_real_conversations_are_kept_apart(client):
    ask(client, "hi", session_id="shared-id")

    assert "shared-id" in main.demo_sessions
    assert "shared-id" not in main.sessions


# --- what it answers -------------------------------------------------------


@pytest.mark.parametrize(
    "question,expected",
    [
        ("What does DEMO-1 say?", "Self-service account closure"),
        ("Tell me about DEMO-7", "First time deposit tracking"),
        ("What stories are needed for DEMO-7?", "first_time_deposit"),
        ("Split DEMO-1 into stories", "Add account closure entry point"),
        ("What's assigned to me and not done?", "2 open items"),
        ("Summarise the current sprint", "Sprint 24"),
        ("What's blocked right now?", "blocked"),
        ("the account closure epic", "Self-service account closure"),
        ("tell me about deposit tracking", "First time deposit tracking"),
    ],
)
def test_questions_reach_the_right_sample_answer(question, expected):
    reply, _ = answer(question)
    assert expected.lower() in reply.lower()


def test_an_unrelated_question_explains_the_limits():
    reply, calls = answer("what is the weather in Vienna")

    assert "demo mode" in reply.lower()
    assert "DEMO-1" in reply and "DEMO-7" in reply
    assert calls == []


def test_replies_show_a_plausible_tool_trail(client):
    data = ask(client, "What's assigned to me and not done?")

    assert data["tool_calls"], "the audit trail should not be empty"
    call = data["tool_calls"][0]
    assert call["name"] == "search_issues"
    assert "currentUser()" in call["args"]["jql"]
    assert call["ok"] is True


async def test_the_demo_agent_keeps_its_own_history():
    agent = DemoAgent()
    await agent.chat("What does DEMO-1 say?")
    assert len(agent.messages) == 2

    agent.reset()
    assert agent.messages == []


# --- the page --------------------------------------------------------------


def test_the_page_switches_to_demo_from_the_url():
    page = Path("app/static/index.html").read_text(encoding="utf-8")

    assert "has('demo')" in page
    assert "demo: demoMode" in page, "the flag must be sent to the server"
    assert "demoBar" in page, "demo mode must be visibly signposted"
    assert "Try demo mode instead" in page, "a key error should offer demo mode"
