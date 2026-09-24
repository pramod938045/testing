import csv
import io

import httpx
import pytest

from storygen.jira import Jira, auth_header, detect_deployment
from storygen.testcases import (
    CSV_COLUMNS,
    build_prompt,
    generate_test_cases,
    render_csv,
    render_markdown,
)

CONTEXT = {
    "key": "ABC-1",
    "summary": "Close an account",
    "type": "Epic",
    "status": "Open",
    "project": "ABC",
    "priority": "Major",
    "labels": ["compliance"],
    "description": "The player must confirm before anything happens.",
    "links": [],
    "subtasks": [],
    "url": "https://jira.example.com/browse/ABC-1",
}

RESULT = {
    "test_cases": [
        {
            "id": "TC-01",
            "title": "Confirmation is required",
            "type": "Functional",
            "priority": "High",
            "covers": "The player must confirm before anything happens",
            "preconditions": ["A logged-in player"],
            "test_data": "",
            "steps": [
                {"action": "Start the flow.", "expected": "A confirmation step appears."},
                {"action": "Abandon it.", "expected": "Nothing changed."},
            ],
            "expected_result": "Nothing happens without confirmation.",
        },
        {
            "id": "TC-02",
            "title": "Pipes | in text do not break the table",
            "type": "Negative",
            "priority": "Low",
            "covers": "n/a",
            "preconditions": [],
            "test_data": "a|b",
            "steps": [{"action": "Enter a|b.", "expected": "Rejected | with an error."}],
            "expected_result": "Rejected.",
        },
    ],
    "open_questions": ["What is the exact error text?"],
    "not_manually_testable": ["Throughput under load."],
}


# ------------------------------------------------------------------ prompting


def test_prompt_carries_the_issue_content():
    text = build_prompt(CONTEXT)
    assert "ABC-1" in text
    assert "The player must confirm" in text
    assert "compliance" in text


def test_prompt_marks_linked_issues_as_context_only():
    context = dict(CONTEXT, links=[
        {"relation": "blocks", "key": "ABC-9", "status": "Open", "summary": "Other work"}
    ])
    text = build_prompt(context)
    assert "do not test these" in text
    assert "ABC-9" in text


# ----------------------------------------------------------------- generation


class FakeBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class FakeResponse:
    stop_reason = "end_turn"

    def __init__(self, text):
        self.content = [FakeBlock(text)]


class FakeMessages:
    def __init__(self, response):
        self._response = response
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self._response


class FakeClient:
    def __init__(self, response):
        self.messages = FakeMessages(response)


def test_generate_parses_the_model_json():
    import json

    client = FakeClient(FakeResponse(json.dumps(RESULT)))
    result = generate_test_cases(CONTEXT, "unused", client=client)
    assert result["test_cases"][0]["id"] == "TC-01"
    assert client.messages.kwargs["output_config"]["format"]["type"] == "json_schema"


def test_generate_rejects_an_empty_response():
    client = FakeClient(FakeResponse("   "))
    with pytest.raises(RuntimeError, match="empty"):
        generate_test_cases(CONTEXT, "unused", client=client)


def test_generate_reports_a_refusal():
    response = FakeResponse("{}")
    response.stop_reason = "refusal"
    with pytest.raises(RuntimeError, match="declined"):
        generate_test_cases(CONTEXT, "unused", client=FakeClient(response))


# ------------------------------------------------------------------ rendering


def test_markdown_has_a_step_table_per_case():
    text = render_markdown("ABC-1", RESULT, CONTEXT["url"])
    assert "# Manual test cases for ABC-1" in text
    assert "TC-01 — Confirmation is required" in text
    assert "| 1 | Start the flow. | A confirmation step appears. |" in text
    assert "**Covers:** The player must confirm before anything happens" in text


def test_markdown_escapes_pipes_so_the_table_survives():
    text = render_markdown("ABC-1", RESULT)
    assert "Enter a\\|b." in text
    assert "Rejected \\| with an error." in text


def test_markdown_lists_open_questions_and_untestable_items():
    text = render_markdown("ABC-1", RESULT)
    assert "What is the exact error text?" in text
    assert "Throughput under load." in text
    assert "Nothing has been created in Jira" in text


def test_csv_has_one_row_per_step():
    rows = list(csv.reader(io.StringIO(render_csv(RESULT))))
    assert rows[0] == CSV_COLUMNS
    # TC-01 has two steps, TC-02 has one.
    assert len(rows) == 1 + 3
    assert rows[1][0] == "TC-01" and rows[1][7] == "1"
    assert rows[2][0] == "TC-01" and rows[2][7] == "2"
    assert rows[3][0] == "TC-02"


def test_csv_repeats_case_columns_on_every_step_row():
    rows = list(csv.reader(io.StringIO(render_csv(RESULT))))
    assert rows[1][1] == rows[2][1] == "Confirmation is required"
    assert rows[1][5] == rows[2][5] == "A logged-in player"


def test_csv_quotes_a_value_containing_a_comma():
    result = {
        "test_cases": [
            {
                "id": "TC-01",
                "title": "One, two",
                "type": "Functional",
                "priority": "Low",
                "covers": "",
                "preconditions": [],
                "test_data": "",
                "steps": [{"action": "Do it", "expected": "Done"}],
                "expected_result": "",
            }
        ]
    }
    rows = list(csv.reader(io.StringIO(render_csv(result))))
    assert rows[1][1] == "One, two"


# ------------------------------------------------------- deployment handling


def test_detect_deployment_reads_the_host():
    assert detect_deployment("https://acme.atlassian.net") == "cloud"
    assert detect_deployment("https://jira.scigames.at") == "server"


def test_data_center_uses_v2_paths_and_a_bearer_token():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["auth"] = request.headers["Authorization"]
        return httpx.Response(200, json={"displayName": "Sam", "name": "sam"})

    jira = Jira("https://jira.example.com", "", "pat-123", deployment="server")
    # Keep the headers the client was built with — they are what is under test.
    jira.http = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://jira.example.com",
        headers=dict(jira.http.headers),
    )
    jira.myself()
    assert seen["path"] == "/rest/api/2/myself"
    assert seen["auth"] == "Bearer pat-123"
    jira.close()


def test_cloud_still_uses_v3_and_basic_auth():
    assert auth_header("cloud", "me@example.com", "t").startswith("Basic ")
    assert auth_header("server", "", "t") == "Bearer t"
    assert Jira("https://acme.atlassian.net", "m@e.com", "t").api == "3"


def test_data_center_search_skips_the_cloud_only_endpoint():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={"issues": []})

    jira = Jira("https://jira.example.com", "", "pat", deployment="server")
    jira.http = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://jira.example.com"
    )
    jira.search("project = ABC", ["summary"])
    assert paths == ["/rest/api/2/search"]
    jira.close()


def test_the_read_client_still_refuses_to_write():
    from storygen.jira import JiraError

    jira = Jira("https://jira.example.com", "", "pat", deployment="server")
    with pytest.raises(JiraError, match="read-only"):
        jira._request("POST", "/rest/api/2/issue/ABC-1/comment", json={})
    jira.close()
