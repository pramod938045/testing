"""Reading an epic and the stories under it."""

import httpx
import pytest

from storygen import main as cli
from storygen.config import Config
from storygen.jira import Jira, JiraError


def make_jira(handler) -> Jira:
    jira = Jira("https://jira.example.com", "", "pat", deployment="server")
    jira.http = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://jira.example.com"
    )
    return jira


def row(key, summary, kind="Story", status="Open"):
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "issuetype": {"name": kind},
            "status": {"name": status},
        },
    }


# ------------------------------------------------------------ find_children


def test_children_found_by_epic_link():
    seen = []

    def handler(request):
        import json

        jql = json.loads(request.content)["jql"]
        seen.append(jql)
        return httpx.Response(200, json={"issues": [row("ABC-2", "Do the thing")]})

    jira = make_jira(handler)
    children, jql = jira.find_children("ABC-1")
    assert [child["key"] for child in children] == ["ABC-2"]
    assert jql.startswith('"Epic Link" = ABC-1')
    assert len(seen) == 1  # stopped at the first field that worked
    jira.close()


def test_falls_through_to_parent_when_epic_link_is_absent():
    def handler(request):
        import json

        jql = json.loads(request.content)["jql"]
        if "Epic Link" in jql:
            return httpx.Response(
                400, json={"errorMessages": ["Field 'Epic Link' does not exist"]}
            )
        return httpx.Response(200, json={"issues": [row("ABC-3", "Newer style child")]})

    jira = make_jira(handler)
    children, jql = jira.find_children("ABC-1")
    assert [child["key"] for child in children] == ["ABC-3"]
    assert jql.startswith("parent = ABC-1")
    jira.close()


def test_an_epic_with_no_children_is_not_an_error():
    jira = make_jira(lambda r: httpx.Response(200, json={"issues": []}))
    children, jql = jira.find_children("ABC-1")
    assert children == [] and jql == ""
    jira.close()


def test_every_field_rejected_raises_rather_than_reporting_empty():
    """All three rejected means the site links epics some other way —
    reporting "no stories" there would be a lie."""
    jira = make_jira(
        lambda r: httpx.Response(400, json={"errorMessages": ["Field does not exist"]})
    )
    with pytest.raises(JiraError):
        jira.find_children("ABC-1")
    jira.close()


# -------------------------------------------------------------- the command


EPIC = {
    "key": "ABC-1",
    "summary": "Subscription funding",
    "type": "Epic",
    "status": "In Progress",
    "project": "ABC",
    "priority": "Major",
    "labels": ["funding"],
    "description": "Collect funds by direct debit.",
    "links": [],
    "subtasks": [],
    "url": "https://jira.example.com/browse/ABC-1",
}

STORY = dict(
    EPIC,
    key="ABC-2",
    summary="Process M602 feedback",
    type="Story",
    description="Read the feedback delivery and settle each record.",
    url="https://jira.example.com/browse/ABC-2",
)


class FakeJira:
    def __init__(self, children):
        self._children = children
        self.fetched = []

    def get_context(self, key):
        self.fetched.append(key)
        return EPIC if key == "ABC-1" else STORY

    def find_children(self, key, limit=100):
        return self._children, '"Epic Link" = ABC-1'

    def close(self):
        pass


def _config() -> Config:
    return Config(
        jira_url="https://jira.example.com",
        jira_email="",
        jira_token="pat",
        anthropic_key="",
    )


def test_bundle_has_the_epic_the_table_and_each_story(monkeypatch, capsys):
    fake = FakeJira([row("ABC-2", "Process M602 feedback")])
    monkeypatch.setattr(cli, "_jira", lambda config: fake)

    assert cli.epic(_config(), "ABC-1") == 0
    out = capsys.readouterr().out
    assert "# ABC-1 — Subscription funding" in out
    assert "Collect funds by direct debit." in out
    assert "| ABC-2 | Story | Open | Process M602 feedback |" in out
    assert "Read the feedback delivery and settle each record." in out
    assert fake.fetched == ["ABC-1", "ABC-2"]


def test_brief_skips_the_per_story_reads(monkeypatch, capsys):
    fake = FakeJira([row("ABC-2", "Process M602 feedback")])
    monkeypatch.setattr(cli, "_jira", lambda config: fake)

    assert cli.epic(_config(), "ABC-1", brief=True) == 0
    out = capsys.readouterr().out
    assert "| ABC-2 | Story | Open | Process M602 feedback |" in out
    assert "Read the feedback delivery" not in out
    assert fake.fetched == ["ABC-1"]


def test_an_empty_epic_says_so_rather_than_printing_nothing(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_jira", lambda config: FakeJira([]))

    assert cli.epic(_config(), "ABC-1") == 0
    assert "_None found._" in capsys.readouterr().out


def test_the_bundle_can_be_saved(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "_jira", lambda config: FakeJira([row("ABC-2", "A story")]))
    target = tmp_path / "epic.md"

    assert cli.epic(_config(), "ABC-1", save=str(target)) == 0
    written = target.read_text(encoding="utf-8")
    assert "# ABC-1 — Subscription funding" in written
    assert "ABC-2" in written


def test_a_lowercase_key_is_normalised(monkeypatch, capsys):
    fake = FakeJira([])
    monkeypatch.setattr(cli, "_jira", lambda config: fake)

    cli.epic(_config(), "  abc-1  ")
    assert fake.fetched == ["ABC-1"]
