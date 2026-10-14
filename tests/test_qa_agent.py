"""QA agent and TestRail client tests — mocked TestRail and Jira, stubbed Claude."""

import json
from typing import Any
from urllib.parse import parse_qsl

import httpx
import pytest

from app.config import Settings
from qa_agent.agent import QAAgent
from qa_agent.config import TestRailSettings
from qa_agent.main import format_proposal
from qa_agent.testrail_client import TestRailClient, TestRailError
from qa_agent.tools import CREATE_TOOL, TESTRAIL_HANDLERS, TESTRAIL_TOOL_SPECS
from tests.test_agent import FakeAnthropic, FakeMessage, FakeMessages, TextBlock, ToolUseBlock

TESTRAIL_URL = "https://acme.testrail.io"

CASE = {"title": "Login with valid password", "steps": [{"action": "Sign in", "expected": "Dashboard shows"}]}


def make_testrail(routes: dict[str, Any], **kwargs: Any) -> tuple[TestRailClient, list[httpx.Request]]:
    """`routes` maps "METHOD endpoint" (e.g. "GET get_case/7") to a payload or (status, payload)."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        route = request.url.query.decode().removeprefix("/api/v2/").split("&", 1)[0]
        key = f"{request.method} {route}"
        if key not in routes:
            return httpx.Response(400, json={"error": f"no route for {key}"})
        entry = routes[key]
        status, payload = entry if isinstance(entry, tuple) else (200, entry)
        return httpx.Response(status, content=json.dumps(payload), headers={"content-type": "application/json"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=TESTRAIL_URL)
    return TestRailClient(TESTRAIL_URL, "qa@example.com", "key", client=http, **kwargs), seen


def filters(request: httpx.Request) -> dict[str, str]:
    return dict(parse_qsl(request.url.query.decode().split("&", 1)[1])) if b"&" in request.url.query else {}


def build_agent(make_client, responses, routes=None, confirm=None):
    jira, _ = make_client({})
    testrail, seen = make_testrail(routes or {})
    agent = QAAgent(
        jira=jira,
        testrail=testrail,
        settings=Settings(jira_base_url="https://example.atlassian.net", anthropic_api_key="k"),
        testrail_settings=TestRailSettings(base_url=TESTRAIL_URL, email="qa@example.com", api_key="key"),
        confirm=confirm,
    )
    agent.client = FakeAnthropic(messages=FakeMessages(responses))
    return agent, seen


def create_call(**overrides):
    args = {"section_id": 5, "jira_key": "abc-1", "cases": [CASE], **overrides}
    return [
        FakeMessage([ToolUseBlock(CREATE_TOOL, args)], stop_reason="tool_use"),
        FakeMessage([TextBlock("done")]),
    ]


SECTION = {"GET get_section/5": {"id": 5, "name": "Login", "suite_id": 1}}


# ------------------------------------------------------------------ client


async def test_find_cases_filters_by_jira_reference():
    client, seen = make_testrail({
        "GET get_cases/1": {
            "cases": [
                {"id": 10, "title": "a", "refs": "ABC-1, ABC-2"},
                {"id": 11, "title": "b", "refs": "ABC-10"},
            ],
            "_links": {"next": None},
        }
    })
    result = await client.find_cases(1, suite_id=3, ref="abc-1")

    assert [case["id"] for case in result["cases"]] == [10]
    assert result["cases"][0]["url"] == f"{TESTRAIL_URL}/index.php?/cases/view/10"
    assert filters(seen[0]) == {"refs": "abc-1", "suite_id": "3"}
    await client.aclose()


async def test_find_cases_retries_without_refs_filter_on_old_testrail():
    calls = []

    def handler(request):
        calls.append(filters(request))
        if "refs" in filters(request):
            return httpx.Response(400, json={"error": "Field :refs is not a supported field."})
        return httpx.Response(200, json=[{"id": 1, "refs": "ABC-1"}, {"id": 2, "refs": None}])

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=TESTRAIL_URL)
    client = TestRailClient(TESTRAIL_URL, "e", "k", client=http)
    result = await client.find_cases(1, ref="ABC-1")

    assert [c["id"] for c in result["cases"]] == [1]
    assert len(calls) == 2
    await client.aclose()


async def test_run_tests_are_counted_by_status():
    client, _ = make_testrail({
        "GET get_tests/9": {"tests": [
            {"id": 1, "case_id": 10, "status_id": 1},
            {"id": 2, "case_id": 11, "status_id": 5},
            {"id": 3, "case_id": 12, "status_id": 5},
        ]}
    })
    result = await client.get_run_tests(9)
    assert result["by_status"] == {"Passed": 1, "Failed": 2}
    await client.aclose()


async def test_error_message_includes_testrail_detail_and_hint():
    client, _ = make_testrail({"GET get_case/7": (401, {"error": "Authentication failed"})})
    with pytest.raises(TestRailError) as exc:
        await client.get_case(7)
    assert "401" in exc.value.message and "Authentication failed" in exc.value.message
    assert "TESTRAIL_API_KEY" in exc.value.message
    await client.aclose()


@pytest.mark.parametrize("method,endpoint", [
    ("POST", "update_case/1"),
    ("POST", "delete_case/1"),
    ("POST", "add_run/1"),
    ("POST", "add_result_for_case/1/2"),
    ("POST", "close_run/1"),
    ("DELETE", "add_case/1"),
])
async def test_every_write_but_add_case_is_refused_before_the_network(method, endpoint):
    client, seen = make_testrail({})
    with pytest.raises(TestRailError, match="Blocked"):
        await client._request(method, endpoint, json={})
    assert seen == []
    await client.aclose()


async def test_add_case_sends_steps_and_jira_reference():
    client, seen = make_testrail({"POST add_case/5": {"id": 99, "title": CASE["title"]}}, template_id=2)
    created = await client.add_case(5, {**CASE, "preconditions": "User exists"}, "ABC-1")

    assert created["url"].endswith("/cases/view/99")
    assert json.loads(seen[0].content) == {
        "title": "Login with valid password",
        "refs": "ABC-1",
        "custom_preconds": "User exists",
        "template_id": 2,
        "custom_steps_separated": [{"content": "Sign in", "expected": "Dashboard shows"}],
    }
    await client.aclose()


def test_text_template_flattens_steps():
    client = TestRailClient(TESTRAIL_URL, "e", "k", steps_format="text")
    body = client.case_body(CASE, "ABC-1")
    assert body["custom_steps"] == "1. Sign in"
    assert body["custom_expected"] == "1. Dashboard shows"


def test_every_testrail_spec_has_a_handler():
    specs = {spec["name"] for spec in TESTRAIL_TOOL_SPECS} - {CREATE_TOOL}
    assert specs == set(TESTRAIL_HANDLERS)


# ------------------------------------------------------------------- agent


async def test_agent_offers_jira_and_testrail_tools(make_client):
    agent, _ = build_agent(make_client, [FakeMessage([TextBlock("hi")])])
    names = {tool["name"] for tool in agent._tools()}
    assert {"get_issue", "search_issues", "testrail_find_cases", CREATE_TOOL} <= names
    await agent.chat("hi")
    system = agent.client.messages.calls[0]["system"]
    assert TESTRAIL_URL in system[1]["text"]


async def test_read_tool_reaches_testrail(make_client):
    responses = [
        FakeMessage([ToolUseBlock("testrail_get_case", {"case_id": 7})], stop_reason="tool_use"),
        FakeMessage([TextBlock("ok")]),
    ]
    agent, seen = build_agent(make_client, responses, {"GET get_case/7": {"id": 7, "title": "x"}})
    result = await agent.chat("show C7")
    assert result.tool_calls[0].ok
    assert len(seen) == 1


async def test_create_without_confirm_callback_writes_nothing(make_client):
    agent, seen = build_agent(make_client, create_call(), SECTION)
    result = await agent.chat("add tests")

    assert all(r.method == "GET" for r in seen)
    assert "declined" in result.tool_calls[0].summary


async def test_declined_proposal_writes_nothing(make_client):
    shown = []

    async def decline(proposal):
        shown.append(proposal)
        return False

    agent, seen = build_agent(make_client, create_call(), SECTION, confirm=decline)
    await agent.chat("add tests")

    assert shown[0]["section"]["name"] == "Login"
    assert shown[0]["jira_key"] == "ABC-1"
    assert shown[0]["cases"] == [CASE]
    assert all(r.method == "GET" for r in seen)


async def test_approved_proposal_creates_each_case(make_client):
    async def approve(proposal):
        return True

    routes = {**SECTION, "POST add_case/5": {"id": 99, "title": CASE["title"]}}
    agent, seen = build_agent(make_client, create_call(cases=[CASE, CASE]), routes, confirm=approve)
    result = await agent.chat("add tests")

    posts = [r for r in seen if r.method == "POST"]
    assert len(posts) == 2
    assert json.loads(posts[0].content)["refs"] == "ABC-1"
    assert result.tool_calls[0].ok


async def test_partial_failure_reports_what_was_created(make_client):
    async def approve(proposal):
        return True

    created = iter([(200, {"id": 1, "title": "a"}), (400, {"error": "Field :title is required"})])

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"id": 5, "name": "Login"})
        status, payload = next(created)
        return httpx.Response(status, json=payload)

    agent, _ = build_agent(make_client, [], confirm=approve)
    agent.testrail = TestRailClient(
        TESTRAIL_URL, "e", "k",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=TESTRAIL_URL),
    )
    result, is_error = await agent._create_cases({"section_id": 5, "jira_key": "ABC-1", "cases": [CASE, CASE]})

    assert is_error
    assert len(result["created"]) == 1
    assert "title is required" in result["error"]


async def test_unknown_section_is_reported_before_asking(make_client):
    asked = []

    async def confirm(proposal):
        asked.append(proposal)
        return True

    agent, _ = build_agent(make_client, [], {"GET get_section/5": (400, {"error": "Field :section_id is not a valid section."})}, confirm)
    result, is_error = await agent._create_cases({"section_id": 5, "jira_key": "ABC-1", "cases": [CASE]})

    assert is_error and "not a valid section" in result
    assert asked == []


def test_proposal_shows_every_step():
    text = format_proposal({"section": {"id": 5, "name": "Login"}, "jira_key": "ABC-1", "cases": [CASE]})
    assert "Login" in text and "ABC-1" in text and "Sign in" in text and "Dashboard shows" in text
