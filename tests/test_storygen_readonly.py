"""The story generator must never change Jira data.

These tests are the guarantee: a write is refused before any request is sent,
so a mistake in future code fails loudly instead of editing a real issue.
"""

import httpx
import pytest

from storygen.jira import Jira, JiraError
from tests.test_storygen_jira import make_jira


def recording_jira():
    """A client whose transport records every request that reaches the network."""
    sent = []

    def handler(request):
        sent.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json={})

    return make_jira(handler), sent


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/rest/api/3/issue"),                     # create an issue
        ("PUT", "/rest/api/3/issue/DFE-1"),                # edit fields
        ("DELETE", "/rest/api/3/issue/DFE-1"),             # delete an issue
        ("POST", "/rest/api/3/issue/DFE-1/comment"),       # add a comment
        ("POST", "/rest/api/3/issue/DFE-1/transitions"),   # change status
        ("POST", "/rest/api/3/issueLink"),                 # link issues
        ("PUT", "/rest/api/3/issue/DFE-1/assignee"),       # reassign
    ],
)
def test_every_write_is_refused_and_never_reaches_jira(method, path):
    jira, sent = recording_jira()

    with pytest.raises(JiraError) as exc:
        jira._request(method, path)

    assert "read-only" in str(exc.value)
    assert sent == [], f"{method} {path} reached the network"
    jira.close()


def test_reads_still_work():
    jira, sent = recording_jira()

    jira._request("GET", "/rest/api/3/issue/DFE-1")
    jira._request("POST", "/rest/api/3/search/jql", json={"jql": "project = DFE"})
    jira._request("POST", "/rest/api/3/search", json={"jql": "project = DFE"})

    assert sent == [
        "GET /rest/api/3/issue/DFE-1",
        "POST /rest/api/3/search/jql",
        "POST /rest/api/3/search",
    ]
    jira.close()


def test_the_client_exposes_no_write_methods():
    """A reviewer's check: nothing on the class is named like a write."""
    forbidden = ("create", "update", "delete", "add_", "transition", "assign", "link_")
    methods = [name for name in dir(Jira) if not name.startswith("__")]

    offenders = [m for m in methods if any(m.startswith(word) for word in forbidden)]
    assert offenders == [], f"unexpected write-shaped methods: {offenders}"
