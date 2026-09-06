import json

import pytest

from app.jira_client import JiraError, escape_jql

ISSUE = {
    "key": "ABC-1",
    "fields": {
        "summary": "Login is broken",
        "status": {"name": "In Progress", "statusCategory": {"name": "In Progress"}},
        "issuetype": {"name": "Bug"},
        "priority": {"name": "High"},
        "assignee": {"displayName": "Sam Patel"},
        "labels": ["auth"],
        "project": {"key": "ABC"},
        "description": {
            "type": "doc",
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "500 on submit"}]}],
        },
    },
}


def test_escape_jql_escapes_quotes_and_backslashes():
    assert escape_jql('say "hi"') == 'say \\"hi\\"'
    assert escape_jql("back\\slash") == "back\\\\slash"


async def test_search_uses_the_jql_endpoint_and_simplifies_issues(make_client):
    client, seen = make_client(
        {"POST /rest/api/3/search/jql": {"issues": [ISSUE], "total": 1, "nextPageToken": "abc"}}
    )
    result = await client.search("project = ABC", max_results=10)

    assert result["count"] == 1
    assert result["total"] == 1
    assert result["next_page_token"] == "abc"
    issue = result["issues"][0]
    assert issue["key"] == "ABC-1"
    assert issue["status"] == "In Progress"
    assert issue["assignee"] == "Sam Patel"
    assert issue["url"] == "https://example.atlassian.net/browse/ABC-1"
    # The summary view stays lean — no description on search results.
    assert "description" not in issue

    body = json.loads(seen[0].content)
    assert body["jql"] == "project = ABC"
    assert body["maxResults"] == 10
    await client.aclose()


async def test_search_falls_back_to_the_legacy_endpoint(make_client):
    client, seen = make_client(
        {
            "POST /rest/api/3/search/jql": (410, {"errorMessages": ["gone"]}),
            "POST /rest/api/3/search": {"issues": [ISSUE]},
        }
    )
    result = await client.search("project = ABC")

    assert result["count"] == 1
    assert [request.url.path for request in seen] == [
        "/rest/api/3/search/jql",
        "/rest/api/3/search",
    ]
    await client.aclose()


async def test_get_issue_includes_the_rendered_description(make_client):
    client, _ = make_client({"GET /rest/api/3/issue/ABC-1": ISSUE})
    issue = await client.get_issue("ABC-1")
    assert issue["description"] == "500 on submit"
    await client.aclose()


async def test_sprint_report_groups_by_status_and_assignee(make_client):
    unassigned = {
        "key": "ABC-2",
        "fields": {"summary": "No owner", "status": {"name": "To Do"}, "issuetype": {"name": "Task"}},
    }
    client, _ = make_client({"GET /rest/agile/1.0/sprint/7/issue": {"issues": [ISSUE, unassigned]}})
    report = await client.sprint_report(7)

    assert report["total"] == 2
    assert report["by_status"] == {"In Progress": 1, "To Do": 1}
    assert report["by_assignee"] == {"Sam Patel": 1, "Unassigned": 1}
    await client.aclose()


async def test_api_errors_carry_the_jira_message_and_a_hint(make_client):
    client, _ = make_client(
        {"GET /rest/api/3/issue/ABC-1": (401, {"errorMessages": ["Client must be authenticated"]})}
    )
    with pytest.raises(JiraError) as exc:
        await client.get_issue("ABC-1")

    assert exc.value.status_code == 401
    assert "Client must be authenticated" in exc.value.message
    assert "JIRA_API_TOKEN" in exc.value.message
    await client.aclose()


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/rest/api/3/issue"),
        ("PUT", "/rest/api/3/issue/ABC-1"),
        ("DELETE", "/rest/api/3/issue/ABC-1"),
        ("POST", "/rest/api/3/issue/ABC-1/comment"),
        ("POST", "/rest/api/3/issue/ABC-1/transitions"),
        ("POST", "/rest/api/3/issue/ABC-1/worklog"),
        ("PUT", "/rest/api/3/issue/ABC-1/assignee"),
    ],
)
async def test_writes_are_refused_and_never_reach_jira(method, path, make_client):
    """The chatbot must not be able to change Jira, whatever the model asks for."""
    client, seen = make_client({})

    with pytest.raises(JiraError) as exc:
        await client._request(method, path)

    assert "read-only" in exc.value.message
    assert seen == [], f"{method} {path} reached the network"
    await client.aclose()


async def test_reads_still_reach_jira(make_client):
    client, seen = make_client(
        {
            "GET /rest/api/3/myself": {"displayName": "Sam Patel"},
            "POST /rest/api/3/search/jql": {"issues": []},
        }
    )
    await client.myself()
    await client.search("project = ABC")

    assert [f"{r.method} {r.url.path}" for r in seen] == [
        "GET /rest/api/3/myself",
        "POST /rest/api/3/search/jql",
    ]
    await client.aclose()


def test_the_client_exposes_no_write_methods():
    from app.jira_client import JiraClient

    forbidden = ("create", "update", "delete", "add_", "transition_issue", "assign", "log_work")
    methods = [m for m in dir(JiraClient) if not m.startswith("__")]
    offenders = [m for m in methods if any(m.startswith(word) for word in forbidden)]
    assert offenders == [], f"unexpected write-shaped methods: {offenders}"
