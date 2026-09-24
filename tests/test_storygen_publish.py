"""The write path, and above all the consent gate in front of it."""

import httpx
import pytest

from storygen import main as cli
from storygen.config import Config
from storygen.publish import JiraWriter, NotConfirmed, render_case, render_plain, text_to_adf

RESULT = {
    "test_cases": [
        {
            "id": "TC-01",
            "title": "Confirmation is required",
            "type": "Functional",
            "priority": "High",
            "covers": "The player must confirm",
            "preconditions": ["A logged-in player"],
            "test_data": "none",
            "steps": [{"action": "Start the flow.", "expected": "A confirmation appears."}],
            "expected_result": "Nothing happens without confirmation.",
        }
    ],
    "open_questions": ["What is the error text?"],
    "not_manually_testable": [],
}

CONTEXT = {
    "key": "ABC-1",
    "project": "ABC",
    "summary": "Close an account",
    "type": "Epic",
    "status": "Open",
    "priority": "",
    "labels": [],
    "description": "The player must confirm.",
    "links": [],
    "subtasks": [],
    "url": "https://jira.example.com/browse/ABC-1",
}


def make_writer(handler, deployment="server") -> JiraWriter:
    writer = JiraWriter("https://jira.example.com", "", "pat", deployment=deployment)
    writer.http = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://jira.example.com"
    )
    return writer


def refuse(request):  # pragma: no cover - a call here means the gate leaked
    raise AssertionError(f"Jira was written to without consent: {request.method} {request.url}")


# --------------------------------------------------------------- the gate


def test_add_comment_refuses_without_confirmation():
    writer = make_writer(refuse)
    with pytest.raises(NotConfirmed):
        writer.add_comment("ABC-1", "body")
    writer.close()


def test_create_subtask_refuses_without_confirmation():
    writer = make_writer(refuse)
    with pytest.raises(NotConfirmed):
        writer.create_subtask("ABC-1", "ABC", "TC-01", "body")
    writer.close()


def test_confirm_accepts_only_a_typed_yes():
    assert cli._confirm("? ", reader=lambda _: "yes") is True
    assert cli._confirm("? ", reader=lambda _: "  YES  ") is True
    assert cli._confirm("? ", reader=lambda _: "y") is False
    assert cli._confirm("? ", reader=lambda _: "") is False
    assert cli._confirm("? ", reader=lambda _: "no") is False


def test_confirm_treats_a_closed_stdin_as_no():
    def closed(_):
        raise EOFError

    assert cli._confirm("? ", reader=closed) is False


def _patch_generation(monkeypatch):
    """Make `testcases` run without Jira or the AI."""
    class FakeJira:
        def get_context(self, key):
            return CONTEXT

        def close(self):
            pass

    monkeypatch.setattr(cli, "_jira", lambda config: FakeJira())
    monkeypatch.setattr(cli, "generate_test_cases", lambda *a, **k: RESULT)


def _config() -> Config:
    return Config(
        jira_url="https://jira.example.com",
        jira_email="",
        jira_token="pat",
        anthropic_key="sk-ant-test",
    )


def test_declining_the_prompt_writes_nothing(monkeypatch, capsys):
    _patch_generation(monkeypatch)
    monkeypatch.setattr(cli, "_confirm", lambda *a, **k: False)
    monkeypatch.setattr(cli, "_write_to_jira", refuse)

    assert cli.testcases(_config(), "ABC-1", post=True) == 0
    assert "Jira is unchanged" in capsys.readouterr().out


def test_without_post_the_prompt_never_appears(monkeypatch, capsys):
    _patch_generation(monkeypatch)
    monkeypatch.setattr(cli, "_confirm", refuse)
    monkeypatch.setattr(cli, "_write_to_jira", refuse)

    assert cli.testcases(_config(), "ABC-1") == 0
    assert "Nothing was written to Jira" in capsys.readouterr().out


def test_accepting_the_prompt_writes(monkeypatch):
    _patch_generation(monkeypatch)
    monkeypatch.setattr(cli, "_confirm", lambda *a, **k: True)
    written = {}

    def record(config, context, result, target):
        written["target"] = target
        written["key"] = context["key"]
        return 0

    monkeypatch.setattr(cli, "_write_to_jira", record)

    assert cli.testcases(_config(), "ABC-1", post=True) == 0
    assert written == {"target": "comment", "key": "ABC-1"}


def test_the_preview_says_what_will_change(monkeypatch, capsys):
    cli._preview_write(_config(), CONTEXT, RESULT, "subtasks")
    out = capsys.readouterr().out
    assert "PERMISSION NEEDED" in out
    assert "create 1 sub-tasks under ABC-1" in out
    assert "TC-01" in out


def test_demo_mode_cannot_post(capsys):
    assert cli.testcases(_config(), "DEMO-1", post=True, demo=True) == 1
    assert "--post needs a real Jira" in capsys.readouterr().err


# ------------------------------------------------------------- the payload


def test_data_center_gets_a_plain_string_body():
    sent = {}

    def handler(request):
        import json

        sent["path"] = request.url.path
        sent["body"] = json.loads(request.content)["body"]
        return httpx.Response(201, json={"id": "10001"})

    writer = make_writer(handler, deployment="server")
    writer.add_comment("ABC-1", "h3. Title\n- a step", confirmed=True)
    assert sent["path"] == "/rest/api/2/issue/ABC-1/comment"
    assert isinstance(sent["body"], str)
    writer.close()


def test_cloud_gets_an_adf_document():
    sent = {}

    def handler(request):
        import json

        sent["path"] = request.url.path
        sent["body"] = json.loads(request.content)["body"]
        return httpx.Response(201, json={"id": "10001"})

    writer = JiraWriter("https://acme.atlassian.net", "me@e.com", "t", deployment="cloud")
    writer.http = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://acme.atlassian.net"
    )
    writer.add_comment("ABC-1", "h3. Title\n- a step", confirmed=True)
    assert sent["path"] == "/rest/api/3/issue/ABC-1/comment"
    assert sent["body"]["type"] == "doc"
    writer.close()


def test_adf_groups_consecutive_bullets_into_one_list():
    doc = text_to_adf("h3. Steps\n- one\n- two\n\nAfter")
    kinds = [node["type"] for node in doc["content"]]
    assert kinds == ["heading", "bulletList", "paragraph"]
    assert len(doc["content"][1]["content"]) == 2


def test_adf_never_returns_an_empty_document():
    assert text_to_adf("")["content"] == [{"type": "paragraph", "content": []}]


def test_subtask_carries_the_parent_and_project():
    sent = {}

    def handler(request):
        import json

        sent["fields"] = json.loads(request.content)["fields"]
        return httpx.Response(201, json={"key": "ABC-2"})

    writer = make_writer(handler)
    writer.create_subtask("ABC-1", "ABC", "TC-01 Title", "body", confirmed=True)
    assert sent["fields"]["parent"]["key"] == "ABC-1"
    assert sent["fields"]["project"]["key"] == "ABC"
    assert sent["fields"]["issuetype"]["name"] == "Sub-task"
    writer.close()


def test_a_long_summary_is_truncated_to_jiras_limit():
    sent = {}

    def handler(request):
        import json

        sent["summary"] = json.loads(request.content)["fields"]["summary"]
        return httpx.Response(201, json={"key": "ABC-2"})

    writer = make_writer(handler)
    writer.create_subtask("ABC-1", "ABC", "x" * 400, "body", confirmed=True)
    assert len(sent["summary"]) == 255
    writer.close()


def test_a_permission_error_is_explained():
    from storygen.jira import JiraError

    writer = make_writer(
        lambda r: httpx.Response(403, json={"errorMessages": ["No create permission"]})
    )
    with pytest.raises(JiraError, match="lacks permission"):
        writer.add_comment("ABC-1", "body", confirmed=True)
    writer.close()


def test_rendered_comment_covers_every_case_and_question():
    text = render_plain(RESULT, "ABC-1")
    assert "TC-01" in text
    assert "Start the flow." in text
    assert "Open questions" in text


def test_rendered_subtask_body_has_the_steps():
    text = render_case(RESULT["test_cases"][0])
    assert "Steps:" in text
    assert "1. Start the flow. → A confirmation appears." in text
    assert "Expected result: Nothing happens without confirmation." in text
