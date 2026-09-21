import httpx

from storygen.adf import to_text
from tests.test_storygen_jira import make_jira


def para(text):
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


ISSUE = {
    "key": "DFE-9067",
    "fields": {
        "summary": "First Time Deposit Tracking",
        "issuetype": {"name": "Change Request"},
        "status": {"name": "In QA"},
        "project": {"key": "DFE"},
        "priority": {"name": "High"},
        "labels": ["payments"],
        "description": {"type": "doc", "content": [para("Track first deposits."), para("Web only.")]},
        "issuelinks": [
            {
                "type": {"outward": "blocks", "inward": "is blocked by"},
                "outwardIssue": {
                    "key": "UPAM-3395",
                    "fields": {"summary": "Sync deletion", "status": {"name": "Review"},
                               "issuetype": {"name": "Change Request"}},
                },
            },
            {
                "type": {"outward": "relates to", "inward": "relates to"},
                "inwardIssue": {
                    "key": "DFE-1000",
                    "fields": {"summary": "Analytics base", "status": {"name": "Closed"},
                               "issuetype": {"name": "Story"}},
                },
            },
        ],
        "subtasks": [{"key": "DFE-9068", "fields": {"summary": "Add GA event"}}],
    },
}


def test_adf_description_becomes_readable_text():
    assert to_text(ISSUE["fields"]["description"]) == "Track first deposits.\n\nWeb only."


def test_adf_handles_lists_and_tables_from_real_descriptions():
    doc = {
        "type": "doc",
        "content": [
            {"type": "bulletList", "content": [
                {"type": "listItem", "content": [para("one")]},
                {"type": "listItem", "content": [para("two")]},
            ]},
            {"type": "table", "content": [
                {"type": "tableRow", "content": [
                    {"type": "tableHeader", "content": [para("Field")]},
                    {"type": "tableHeader", "content": [para("Value")]},
                ]},
                {"type": "tableRow", "content": [
                    {"type": "tableCell", "content": [para("Limit")]},
                    {"type": "tableCell", "content": [para("500")]},
                ]},
            ]},
        ],
    }
    text = to_text(doc)
    assert "- one" in text and "- two" in text
    assert "Field | Value" in text
    assert "Limit | 500" in text


def test_missing_description_is_empty_not_an_error():
    assert to_text(None) == ""


def test_long_descriptions_are_cut_with_a_marker():
    doc = {"type": "doc", "content": [para("x" * 500)]}
    out = to_text(doc, limit=100)
    assert out.endswith("characters total]")
    assert len(out) < 200


def test_get_context_collects_description_links_and_subtasks():
    jira = make_jira(lambda request: httpx.Response(200, json=ISSUE))
    context = jira.get_context("DFE-9067")

    assert context["summary"] == "First Time Deposit Tracking"
    assert context["type"] == "Change Request"
    assert context["project"] == "DFE"
    assert context["description"] == "Track first deposits.\n\nWeb only."
    assert context["url"] == "https://example.atlassian.net/browse/DFE-9067"

    # Direction decides which relation wording applies.
    assert context["links"][0] == {
        "relation": "blocks", "key": "UPAM-3395", "summary": "Sync deletion",
        "status": "Review", "type": "Change Request",
    }
    assert context["links"][1]["relation"] == "relates to"
    assert context["links"][1]["key"] == "DFE-1000"
    assert context["subtasks"] == [{"key": "DFE-9068", "summary": "Add GA event"}]
    jira.close()


def test_get_context_survives_an_issue_with_nothing_optional_set():
    bare = {"key": "DFE-1", "fields": {"summary": "Bare", "status": {"name": "New"}}}
    jira = make_jira(lambda request: httpx.Response(200, json=bare))
    context = jira.get_context("DFE-1")

    assert context["description"] == ""
    assert context["links"] == [] and context["subtasks"] == []
    assert context["priority"] == "" and context["labels"] == []
    jira.close()


def test_get_context_requests_the_fields_it_needs():
    seen = {}

    def handler(request):
        seen["fields"] = request.url.params["fields"]
        return httpx.Response(200, json=ISSUE)

    jira = make_jira(handler)
    jira.get_context("DFE-9067")
    for field in ("description", "issuelinks", "subtasks", "summary"):
        assert field in seen["fields"]
    jira.close()
