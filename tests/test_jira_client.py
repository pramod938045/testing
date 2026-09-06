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


async def test_create_issue_sends_adf_description(make_client):
    client, seen = make_client({"POST /rest/api/3/issue": {"key": "ABC-9"}})
    result = await client.create_issue(
        project_key="ABC", summary="New task", issue_type="Task", description="line one"
    )

    assert result == {
        "created": True,
        "key": "ABC-9",
        "url": "https://example.atlassian.net/browse/ABC-9",
    }
    fields = json.loads(seen[0].content)["fields"]
    assert fields["project"] == {"key": "ABC"}
    assert fields["issuetype"] == {"name": "Task"}
    assert fields["description"]["type"] == "doc"
    await client.aclose()


async def test_update_issue_only_sends_the_changed_fields(make_client):
    client, seen = make_client({"PUT /rest/api/3/issue/ABC-1": None})
    result = await client.update_issue("ABC-1", priority="Low")

    assert result["changed"] == ["priority"]
    assert json.loads(seen[0].content) == {"fields": {"priority": {"name": "Low"}}}
    await client.aclose()


async def test_update_issue_without_fields_is_rejected(make_client):
    client, seen = make_client({})
    with pytest.raises(JiraError):
        await client.update_issue("ABC-1")
    assert seen == []
    await client.aclose()


async def test_transition_issue_resolves_the_transition_id(make_client):
    client, seen = make_client(
        {
            "GET /rest/api/3/issue/ABC-1/transitions": {
                "transitions": [{"id": "31", "name": "Done", "to": {"name": "Done"}}]
            },
            "POST /rest/api/3/issue/ABC-1/transitions": None,
        }
    )
    result = await client.transition_issue("ABC-1", "done")  # case-insensitive

    assert result["transitioned"] is True
    assert json.loads(seen[1].content) == {"transition": {"id": "31"}}
    await client.aclose()


async def test_transition_issue_lists_the_options_when_there_is_no_match(make_client):
    client, _ = make_client(
        {
            "GET /rest/api/3/issue/ABC-1/transitions": {
                "transitions": [{"id": "21", "name": "Start", "to": {"name": "In Progress"}}]
            }
        }
    )
    with pytest.raises(JiraError) as exc:
        await client.transition_issue("ABC-1", "Released")
    assert "In Progress" in exc.value.message
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


async def test_field_level_errors_are_reported(make_client):
    client, _ = make_client(
        {"POST /rest/api/3/issue": (400, {"errors": {"summary": "Summary is required."}})}
    )
    with pytest.raises(JiraError) as exc:
        await client.create_issue("ABC", "")
    assert "summary: Summary is required." in exc.value.message
    await client.aclose()


async def test_log_work_posts_a_worklog(make_client):
    client, seen = make_client(
        {"POST /rest/api/3/issue/ABC-1/worklog": {"id": "100", "timeSpent": "2h"}}
    )
    result = await client.log_work("ABC-1", "2h", comment="pairing")

    assert result["logged"] is True
    body = json.loads(seen[0].content)
    assert body["timeSpent"] == "2h"
    assert body["comment"]["type"] == "doc"
    await client.aclose()
