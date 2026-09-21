import json

import httpx
import pytest

from storygen.jira import Jira, JiraError, escape_jql, looks_like_key
from tests.test_storygen_jira import make_jira

EPIC = {
    "key": "ABC-42",
    "fields": {
        "summary": "Payments revamp",
        "status": {"name": "In Progress"},
        "issuetype": {"name": "Epic"},
    },
}


def test_issue_keys_are_recognised():
    assert looks_like_key("ABC-42")
    assert looks_like_key("abc-42")  # lowercase is accepted, upcased before use
    assert not looks_like_key("payments")
    assert not looks_like_key("ABC")
    assert not looks_like_key("payments-for-abc")


def test_escape_jql_protects_quotes():
    assert escape_jql('say "hi"') == 'say \\"hi\\"'


def test_find_parents_filters_to_epics_and_change_requests():
    sent = {}

    def handler(request):
        sent["body"] = json.loads(request.content)
        return httpx.Response(200, json={"issues": [EPIC]})

    jira = make_jira(handler)
    issues, type_filtered = jira.find_parents("payments")

    assert type_filtered is True
    assert issues[0]["key"] == "ABC-42"
    jql = sent["body"]["jql"]
    assert 'issuetype in ("Epic", "Change Request")' in jql
    assert 'summary ~ "payments"' in jql
    jira.close()


def test_find_parents_falls_back_when_the_site_has_no_such_issue_types():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content)["jql"])
        if len(calls) == 1:
            return httpx.Response(
                400,
                json={"errorMessages": ["The value 'Change Request' does not exist for issuetype"]},
            )
        return httpx.Response(200, json={"issues": [EPIC]})

    jira = make_jira(handler)
    issues, type_filtered = jira.find_parents("payments")

    assert type_filtered is False
    assert len(issues) == 1
    assert "issuetype in" not in calls[1]
    jira.close()


def test_other_search_errors_are_not_swallowed():
    jira = make_jira(lambda r: httpx.Response(400, json={"errorMessages": ["Bad JQL near 'x'"]}))
    with pytest.raises(JiraError):
        jira.find_parents("payments")
    jira.close()


def test_search_falls_back_to_the_legacy_endpoint():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path.endswith("/search/jql"):
            return httpx.Response(410, json={"errorMessages": ["gone"]})
        return httpx.Response(200, json={"issues": [EPIC]})

    jira = make_jira(handler)
    assert len(jira.search("project = ABC", ["summary"])) == 1
    assert paths == ["/rest/api/3/search/jql", "/rest/api/3/search"]
    jira.close()


def test_get_issue_requests_the_named_fields():
    def handler(request):
        assert request.url.params["fields"] == "summary,status"
        return httpx.Response(200, json=EPIC)

    jira = make_jira(handler)
    assert jira.get_issue("ABC-42", ["summary", "status"])["key"] == "ABC-42"
    jira.close()
