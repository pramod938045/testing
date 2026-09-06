"""Ticket lookup: reads one issue and returns everything, with no AI involved."""

import httpx
import pytest

ISSUE = {
    "key": "DFE-9067",
    "fields": {
        "summary": "First Time Deposit Tracking (Web - Google Analytics)",
        "issuetype": {"name": "Change Request"},
        "status": {"name": "In QA", "statusCategory": {"name": "In Progress"}},
        "priority": {"name": "Major"},
        "project": {"name": "Digital Front End", "key": "DFE"},
        "assignee": {"displayName": "Sam Patel"},
        "reporter": {"displayName": "Pramod Rayanagoudra"},
        "created": "2026-08-01T09:00:00.000+0000",
        "updated": "2026-09-01T15:30:00.000+0000",
        "labels": ["payments"],
        "components": [{"name": "Web"}],
        "fixVersions": [{"name": "2026.9"}],
        "description": {
            "type": "doc",
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Track first deposits."}]}],
        },
        "subtasks": [
            {"key": "DFE-9068", "fields": {"summary": "Add GA event", "status": {"name": "Done"}}}
        ],
        "issuelinks": [
            {
                "type": {"outward": "blocks", "inward": "is blocked by"},
                "outwardIssue": {
                    "key": "UPAM-3395",
                    "fields": {"summary": "Sync deletion", "status": {"name": "Review"}},
                },
            }
        ],
        "comment": {
            "comments": [
                {
                    "author": {"displayName": "Ana Silva"},
                    "created": "2026-08-15T11:00:00.000+0000",
                    "body": {
                        "type": "doc",
                        "content": [
                            {"type": "paragraph", "content": [{"type": "text", "text": "Ready for QA."}]}
                        ],
                    },
                }
            ]
        },
    },
}


async def test_lookup_returns_every_part_of_the_issue(make_client):
    client, _ = make_client({"GET /rest/api/3/issue/DFE-9067": ISSUE})
    data = await client.get_issue_full("DFE-9067")

    assert data["key"] == "DFE-9067"
    assert data["summary"].startswith("First Time Deposit Tracking")
    assert data["type"] == "Change Request"
    assert data["status"] == "In QA"
    assert data["priority"] == "Major"
    assert data["assignee"] == "Sam Patel"
    assert data["reporter"] == "Pramod Rayanagoudra"
    assert data["project"] == "Digital Front End"
    assert data["project_key"] == "DFE"
    assert data["labels"] == ["payments"]
    assert data["components"] == ["Web"]
    assert data["fix_versions"] == ["2026.9"]
    assert data["description"] == "Track first deposits."
    assert data["url"].endswith("/browse/DFE-9067")
    await client.aclose()


async def test_lookup_includes_subtasks_links_and_comments(make_client):
    client, _ = make_client({"GET /rest/api/3/issue/DFE-9067": ISSUE})
    data = await client.get_issue_full("DFE-9067")

    assert data["subtasks"][0]["key"] == "DFE-9068"
    assert data["subtasks"][0]["status"] == "Done"
    assert data["links"][0]["relation"] == "blocks"
    assert data["links"][0]["key"] == "UPAM-3395"
    assert data["comments"][0]["author"] == "Ana Silva"
    assert data["comments"][0]["body"] == "Ready for QA."
    await client.aclose()


async def test_lookup_needs_only_one_request(make_client):
    """Comments arrive with the issue, so a lookup is a single round trip."""
    client, seen = make_client({"GET /rest/api/3/issue/DFE-9067": ISSUE})
    await client.get_issue_full("DFE-9067")

    assert len(seen) == 1
    fields = seen[0].url.params["fields"]
    for field in ("description", "issuelinks", "subtasks", "comment", "reporter"):
        assert field in fields
    await client.aclose()


async def test_lookup_copes_with_a_sparse_issue(make_client):
    bare = {"key": "DFE-1", "fields": {"summary": "Bare", "status": {"name": "New"}}}
    client, _ = make_client({"GET /rest/api/3/issue/DFE-1": bare})
    data = await client.get_issue_full("DFE-1")

    assert data["description"] == ""
    assert data["assignee"] == "Unassigned"
    assert data["subtasks"] == [] and data["links"] == [] and data["comments"] == []
    await client.aclose()


@pytest.mark.parametrize("bad", ["not-a-key", "DFE", "12345", "../etc/passwd"])
def test_the_api_rejects_things_that_are_not_issue_keys(bad):
    from app.main import ISSUE_KEY_RE

    assert not ISSUE_KEY_RE.match(bad.upper())


@pytest.mark.parametrize("good", ["DFE-9067", "UPAM-3395", "AB1-2"])
def test_the_api_accepts_real_issue_keys(good):
    from app.main import ISSUE_KEY_RE

    assert ISSUE_KEY_RE.match(good)


async def test_lookup_reads_only(make_client):
    """A lookup must never write, whatever else changes."""
    client, seen = make_client({"GET /rest/api/3/issue/DFE-9067": ISSUE})
    await client.get_issue_full("DFE-9067")

    assert all(request.method == "GET" for request in seen)
    await client.aclose()


def test_the_lookup_page_exists_and_needs_no_ai_key():
    from pathlib import Path

    page = Path("app/static/lookup.html").read_text(encoding="utf-8")
    assert "/api/issue/" in page
    assert "no AI key needed" in page
    # It must not call the chat endpoint, which is the part that needs a key.
    assert "/api/chat" not in page
