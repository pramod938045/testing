"""The MCP server, and above all that it cannot write to Jira unasked."""

import asyncio
import csv
import io
import json

import pytest

from storygen import mcp_server as srv
from storygen.config import Config, ConfigError
from storygen.jira import JiraError

CASES_JSON = json.dumps(
    {
        "test_cases": [
            {
                "id": "TC-01",
                "title": "Confirmation is required",
                "type": "Functional",
                "priority": "High",
                "covers": "The player must confirm",
                "preconditions": ["Logged in"],
                "test_data": "",
                "steps": [{"action": "Start it.", "expected": "A prompt appears."}],
                "expected_result": "Nothing happens without confirmation.",
            }
        ],
        "open_questions": [],
        "not_manually_testable": [],
    }
)

ISSUE = {
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


class FakeJira:
    def __init__(self, children=None):
        self._children = children or []
        self.closed = False

    def get_context(self, key):
        return dict(ISSUE, key=key)

    def find_children(self, key, limit=100):
        return self._children, '"Epic Link" = ABC-1'

    def find_parents(self, query, limit=20):
        return [
            {
                "key": "ABC-1",
                "fields": {
                    "summary": "Close an account",
                    "issuetype": {"name": "Epic"},
                    "status": {"name": "Open"},
                },
            }
        ], True

    def close(self):
        self.closed = True


def _config() -> Config:
    return Config(
        jira_url="https://jira.example.com",
        jira_email="",
        jira_token="pat",
        anthropic_key="",
    )


@pytest.fixture
def jira(monkeypatch):
    fake = FakeJira()
    monkeypatch.setattr(srv, "_client", lambda: fake)
    monkeypatch.setattr(srv, "_config", _config)
    return fake


@pytest.fixture(autouse=True)
def writes_off(monkeypatch):
    """Every test starts with writing disabled, as a fresh server would."""
    monkeypatch.delenv(srv.WRITES_ENABLED, raising=False)


def explode(*args, **kwargs):  # pragma: no cover - reaching this is the bug
    raise AssertionError("Jira was contacted when it should not have been")


# --------------------------------------------------------------- the tools


def test_the_server_exposes_one_write_tool_and_the_rest_read_only():
    tools = asyncio.run(srv.server.list_tools())
    by_name = {tool.name: tool for tool in tools}
    assert set(by_name) == {
        "jira_issue",
        "jira_epic",
        "jira_find",
        "jira_test_case_brief",
        "test_cases_to_csv",
        "jira_publish_test_cases",
    }
    writers = [
        name for name, tool in by_name.items() if not tool.annotations.read_only_hint
    ]
    assert writers == ["jira_publish_test_cases"]
    assert by_name["jira_publish_test_cases"].annotations.destructive_hint is True


def test_issue_comes_back_as_markdown(jira):
    out = srv.jira_issue("abc-1")
    assert "## ABC-1 — Close an account" in out
    assert "The player must confirm before anything happens." in out
    assert jira.closed


def test_epic_lists_its_children(monkeypatch):
    child = {
        "key": "ABC-2",
        "fields": {
            "summary": "A story",
            "issuetype": {"name": "Story"},
            "status": {"name": "Open"},
        },
    }
    monkeypatch.setattr(srv, "_client", lambda: FakeJira([child]))
    monkeypatch.setattr(srv, "_config", _config)

    out = srv.jira_epic("ABC-1")
    assert "| ABC-2 | Story | Open | A story |" in out
    assert "Stories in full" in out


def test_epic_can_skip_the_descriptions(monkeypatch):
    child = {
        "key": "ABC-2",
        "fields": {
            "summary": "A story",
            "issuetype": {"name": "Story"},
            "status": {"name": "Open"},
        },
    }
    monkeypatch.setattr(srv, "_client", lambda: FakeJira([child]))
    monkeypatch.setattr(srv, "_config", _config)

    out = srv.jira_epic("ABC-1", include_descriptions=False)
    assert "| ABC-2 |" in out
    assert "Stories in full" not in out


def test_find_returns_a_table(jira):
    assert "| ABC-1 | Epic | Open | Close an account |" in srv.jira_find("account")


def test_the_brief_carries_the_rules_the_ticket_and_the_schema(jira):
    out = srv.jira_test_case_brief("ABC-1")
    assert "Never invent scope" in out          # the QA rules
    assert "The player must confirm" in out      # the ticket
    assert '"not_manually_testable"' in out      # the return schema


def test_the_brief_warns_when_the_ticket_has_no_description(monkeypatch):
    class Empty(FakeJira):
        def get_context(self, key):
            return dict(ISSUE, description="")

    monkeypatch.setattr(srv, "_client", lambda: Empty())
    monkeypatch.setattr(srv, "_config", _config)
    assert "no description" in srv.jira_test_case_brief("ABC-1")


def test_csv_conversion_needs_no_jira():
    rows = list(csv.reader(io.StringIO(srv.test_cases_to_csv(CASES_JSON))))
    assert rows[0][0] == "ID"
    assert rows[1][0] == "TC-01"


def test_csv_conversion_rejects_rubbish():
    assert "not valid JSON" in srv.test_cases_to_csv("{nope")
    assert "test_cases" in srv.test_cases_to_csv('{"other": 1}')


# ----------------------------------------------------------- the write gate


def test_dry_run_is_the_default_and_writes_nothing(monkeypatch):
    monkeypatch.setattr(srv, "JiraWriter", explode)
    out = srv.jira_publish_test_cases("ABC-1", CASES_JSON)
    assert "PREVIEW ONLY" in out
    assert "nothing was written" in out
    assert "TC-01" in out


def test_the_preview_admits_that_writing_is_disabled(monkeypatch):
    monkeypatch.setattr(srv, "JiraWriter", explode)
    out = srv.jira_publish_test_cases("ABC-1", CASES_JSON)
    assert f"NO — {srv.WRITES_ENABLED} is not set" in out


def test_a_real_write_is_refused_without_the_environment_variable(monkeypatch):
    monkeypatch.setattr(srv, "JiraWriter", explode)
    out = srv.jira_publish_test_cases("ABC-1", CASES_JSON, dry_run=False)
    assert srv.WRITES_ENABLED in out
    assert "switched off" in out


def test_the_refusal_holds_for_subtasks_too(monkeypatch):
    monkeypatch.setattr(srv, "JiraWriter", explode)
    out = srv.jira_publish_test_cases("ABC-1", CASES_JSON, as_subtasks=True, dry_run=False)
    assert "switched off" in out


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe"])
def test_only_a_truthy_flag_enables_writing(monkeypatch, value):
    monkeypatch.setenv(srv.WRITES_ENABLED, value)
    monkeypatch.setattr(srv, "JiraWriter", explode)
    assert "switched off" in srv.jira_publish_test_cases("ABC-1", CASES_JSON, dry_run=False)


class FakeWriter:
    def __init__(self, *args, **kwargs):
        self.comments = []
        self.subtasks = []
        FakeWriter.last = self

    def add_comment(self, key, text, confirmed=False):
        assert confirmed, "the writer was called without confirmation"
        self.comments.append((key, text))
        return {"id": "10001"}

    def create_subtask(self, parent_key, project_key, summary, description, confirmed=False, **kw):
        assert confirmed, "the writer was called without confirmation"
        self.subtasks.append(summary)
        return {"key": f"ABC-{len(self.subtasks) + 1}"}

    def close(self):
        pass


def test_writing_works_once_it_is_enabled(monkeypatch):
    monkeypatch.setenv(srv.WRITES_ENABLED, "1")
    monkeypatch.setattr(srv, "_config", _config)
    monkeypatch.setattr(srv, "JiraWriter", FakeWriter)

    out = srv.jira_publish_test_cases("ABC-1", CASES_JSON, dry_run=False)
    assert "Posted comment 10001 on ABC-1" in out
    assert FakeWriter.last.comments[0][0] == "ABC-1"
    assert "TC-01" in FakeWriter.last.comments[0][1]


def test_subtasks_are_created_one_per_case(monkeypatch):
    monkeypatch.setenv(srv.WRITES_ENABLED, "1")
    monkeypatch.setattr(srv, "_config", _config)
    monkeypatch.setattr(srv, "JiraWriter", FakeWriter)

    out = srv.jira_publish_test_cases("ABC-1", CASES_JSON, as_subtasks=True, dry_run=False)
    assert "Created 1 sub-tasks" in out
    assert FakeWriter.last.subtasks == ["TC-01 Confirmation is required"]


def test_a_partial_subtask_failure_names_what_survived(monkeypatch):
    monkeypatch.setenv(srv.WRITES_ENABLED, "1")
    monkeypatch.setattr(srv, "_config", _config)

    class Failing(FakeWriter):
        def create_subtask(self, *args, **kwargs):
            if not self.subtasks:
                self.subtasks.append("first")
                return {"key": "ABC-2"}
            raise JiraError("Jira 403: no permission")

    monkeypatch.setattr(srv, "JiraWriter", Failing)
    two_cases = json.loads(CASES_JSON)
    two_cases["test_cases"].append(dict(two_cases["test_cases"][0], id="TC-02"))

    out = srv.jira_publish_test_cases(
        "ABC-1", json.dumps(two_cases), as_subtasks=True, dry_run=False
    )
    assert "ABC-2" in out and "remain in Jira" in out


def test_bad_input_is_refused_before_anything_else(monkeypatch):
    monkeypatch.setenv(srv.WRITES_ENABLED, "1")
    monkeypatch.setattr(srv, "JiraWriter", explode)

    assert "not valid JSON" in srv.jira_publish_test_cases("ABC-1", "{nope", dry_run=False)
    assert "no test cases" in srv.jira_publish_test_cases(
        "ABC-1", '{"test_cases": []}', dry_run=False
    )


# ------------------------------------------------------------- error paths


def test_a_missing_setting_is_reported_as_a_sentence(monkeypatch):
    def unconfigured():
        raise ConfigError("Missing: JIRA_BASE_URL")

    monkeypatch.setattr(srv, "_client", unconfigured)
    out = srv.jira_issue("ABC-1")
    assert out.startswith("Configuration problem:")
    assert "JIRA_BASE_URL" in out


def test_a_jira_failure_is_reported_as_a_sentence(monkeypatch):
    class Broken(FakeJira):
        def get_context(self, key):
            raise JiraError("Jira 404: Not found — check the key.")

    monkeypatch.setattr(srv, "_client", lambda: Broken())
    out = srv.jira_issue("ABC-999")
    assert out.startswith("Jira error:")
    assert "404" in out
