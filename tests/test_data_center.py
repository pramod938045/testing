"""Jira Data Center / Server support.

UPAMCORE-30728 lives on a self-hosted Jira (jira.scigames.at), not on the
Cloud site. Data Center differs from Cloud in four ways that matter: REST v2
not v3, a bearer personal access token not Basic auth, no /search/jql, and a
plain project list instead of /project/search. Rich text comes back as a
string rather than ADF, which `render()` already passes through.
"""

import httpx
import pytest

from app.config import Settings
from app.jira_client import JiraClient, detect_deployment

SERVER_URL = "https://jira.scigames.at"
CLOUD_URL = "https://scientificgames.atlassian.net"

DC_ISSUE = {
    "key": "UPAMCORE-30728",
    "fields": {
        "summary": "Player wallet balance mismatch",
        "issuetype": {"name": "Bug"},
        "status": {"name": "In Progress"},
        "assignee": {"displayName": "Ana Silva"},
        "project": {"name": "UPAM Core", "key": "UPAMCORE"},
        # Data Center returns wiki markup as a plain string, not ADF.
        "description": "Balance is not restored when a withdrawal fails.",
        "issuelinks": [],
        "subtasks": [],
    },
}


def client_for(url, handler, deployment="auto"):
    jira = JiraClient(url, "me@example.com", "token-123", deployment=deployment)
    jira._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=url)
    return jira


def recorder(payload):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=payload)

    return handler, seen


# --- detecting which Jira this is ------------------------------------------


@pytest.mark.parametrize("url,expected", [
    ("https://scientificgames.atlassian.net", "cloud"),
    ("https://ACME.atlassian.net/", "cloud"),
    ("https://jira.scigames.at", "server"),
    ("https://jira.internal.example.com", "server"),
    ("http://localhost:8080", "server"),
])
def test_deployment_is_detected_from_the_site_url(url, expected):
    assert detect_deployment(url) == expected


def test_the_setting_can_override_the_detection():
    jira = JiraClient(SERVER_URL, "a@b.com", "t", deployment="cloud")
    assert jira.deployment == "cloud" and jira.api == "3"


# --- the four differences ---------------------------------------------------


def test_data_center_uses_api_v2_and_cloud_uses_v3():
    assert JiraClient(SERVER_URL, "a@b.com", "t").path("myself") == "/rest/api/2/myself"
    assert JiraClient(CLOUD_URL, "a@b.com", "t").path("myself") == "/rest/api/3/myself"


async def test_data_center_authenticates_with_a_bearer_token():
    handler, seen = recorder(DC_ISSUE)
    jira = client_for(SERVER_URL, handler)
    # The mock transport replaces the client, so check the header the real one carries.
    real = JiraClient(SERVER_URL, "me@example.com", "token-123")
    assert real._client.headers["Authorization"] == "Bearer token-123"
    await real.aclose()
    await jira.aclose()


async def test_cloud_still_authenticates_with_basic():
    real = JiraClient(CLOUD_URL, "me@example.com", "token-123")
    authorization = real._client.headers["Authorization"]

    assert authorization.startswith("Basic ")
    import base64

    assert base64.b64decode(authorization.split()[1]).decode() == "me@example.com:token-123"
    await real.aclose()


async def test_data_center_search_skips_the_cloud_only_endpoint():
    handler, seen = recorder({"issues": []})
    jira = client_for(SERVER_URL, handler)
    await jira.search("project = UPAMCORE")

    assert [request.url.path for request in seen] == ["/rest/api/2/search"]
    await jira.aclose()


async def test_data_center_lists_projects_from_the_plain_list_endpoint():
    projects = [
        {"key": "UPAMCORE", "name": "UPAM Core", "id": "1"},
        {"key": "OTHER", "name": "Other", "id": "2"},
    ]
    handler, seen = recorder(projects)
    jira = client_for(SERVER_URL, handler)
    result = await jira.list_projects()

    assert seen[0].url.path == "/rest/api/2/project"
    assert {p["key"] for p in result["projects"]} == {"UPAMCORE", "OTHER"}
    await jira.aclose()


async def test_data_center_project_filtering_happens_locally():
    handler, _ = recorder([
        {"key": "UPAMCORE", "name": "UPAM Core", "id": "1"},
        {"key": "OTHER", "name": "Other", "id": "2"},
    ])
    jira = client_for(SERVER_URL, handler)
    result = await jira.list_projects(query="upamcore")

    assert [p["key"] for p in result["projects"]] == ["UPAMCORE"]
    await jira.aclose()


async def test_data_center_user_search_uses_username():
    handler, seen = recorder([])
    jira = client_for(SERVER_URL, handler)
    await jira.find_user("pramod")

    assert seen[0].url.params["username"] == "pramod"
    await jira.aclose()


# --- reading a real Data Center issue --------------------------------------


async def test_a_data_center_issue_is_read_the_same_way():
    handler, seen = recorder(DC_ISSUE)
    jira = client_for(SERVER_URL, handler)
    issue = await jira.get_issue_full("UPAMCORE-30728")

    assert seen[0].url.path == "/rest/api/2/issue/UPAMCORE-30728"
    assert issue["key"] == "UPAMCORE-30728"
    assert issue["status"] == "In Progress"
    assert issue["assignee"] == "Ana Silva"
    # Wiki-markup descriptions come through as text, not as an empty ADF parse.
    assert issue["description"] == "Balance is not restored when a withdrawal fails."
    assert issue["url"] == "https://jira.scigames.at/browse/UPAMCORE-30728"
    await jira.aclose()


async def test_data_center_stays_read_only():
    from app.jira_client import JiraError

    handler, seen = recorder({})
    jira = client_for(SERVER_URL, handler)

    for method, path in [("POST", "/rest/api/2/issue"), ("PUT", "/rest/api/2/issue/UPAMCORE-1")]:
        with pytest.raises(JiraError, match="read-only"):
            await jira._request(method, path)
    assert seen == []
    await jira.aclose()


# --- configuration ----------------------------------------------------------


def test_data_center_does_not_require_an_email():
    """A personal access token authenticates on its own."""
    Settings(
        jira_base_url=SERVER_URL, jira_email="", jira_api_token="pat", anthropic_api_key=""
    ).validate()


def test_cloud_still_requires_the_email():
    from app.config import ConfigError

    with pytest.raises(ConfigError, match="JIRA_EMAIL"):
        Settings(
            jira_base_url=CLOUD_URL, jira_email="", jira_api_token="t", anthropic_api_key=""
        ).validate()
