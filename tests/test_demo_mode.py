"""Demo mode: sample data, no Jira and no API key.

The point of these tests is the guarantee, not the sample text: with --demo
nothing may reach Jira or Anthropic, and the command must work with no
settings configured at all.
"""

import pytest

from storygen import main as cli
from storygen.config import Config
from storygen.demo_data import DEMO_ISSUES, DEMO_STORIES, get_demo_issue, get_demo_stories

EMPTY = Config(jira_url="", jira_email="", jira_token="", anthropic_key="")


@pytest.fixture(autouse=True)
def explode_on_real_calls(monkeypatch):
    """Any attempt to reach Jira or Anthropic during a demo run fails the test."""

    def no_jira(*args, **kwargs):
        raise AssertionError("demo mode must not construct a Jira client")

    def no_ai(*args, **kwargs):
        raise AssertionError("demo mode must not call the AI")

    monkeypatch.setattr(cli, "Jira", no_jira)
    monkeypatch.setattr(cli, "generate_stories", no_ai)


# --- the guarantee ---------------------------------------------------------


@pytest.mark.parametrize("argv", [
    ["find", "deposit", "--demo"],
    ["show", "DEMO-1", "--demo"],
    ["generate", "DEMO-7", "--demo"],
    ["prompt", "DEMO-1", "--demo"],
])
def test_every_demo_command_runs_with_no_settings(argv, monkeypatch, capsys):
    monkeypatch.setattr(cli.Config, "load", staticmethod(lambda: EMPTY))

    assert cli.main(argv) == 0
    assert capsys.readouterr().out.strip()


def test_the_same_commands_without_demo_still_need_settings(monkeypatch, capsys):
    """Demo mode is opt-in; the real path is unchanged."""
    monkeypatch.setattr(cli.Config, "load", staticmethod(lambda: EMPTY))

    assert cli.main(["show", "DEMO-1"]) == 1
    assert "JIRA_BASE_URL" in capsys.readouterr().err


# --- what it shows ---------------------------------------------------------


def test_demo_output_is_labelled_as_sample(monkeypatch, capsys):
    monkeypatch.setattr(cli.Config, "load", staticmethod(lambda: EMPTY))

    cli.main(["show", "DEMO-1", "--demo"])
    assert "[demo]" in capsys.readouterr().out

    cli.main(["generate", "DEMO-7", "--demo"])
    out = capsys.readouterr().out
    assert "[demo]" in out
    assert "no Jira, no AI call" in out


def test_generate_renders_stories_criteria_and_questions(monkeypatch, capsys):
    monkeypatch.setattr(cli.Config, "load", staticmethod(lambda: EMPTY))
    cli.main(["generate", "DEMO-1", "--demo"])
    out = capsys.readouterr().out

    assert "# Suggested Stories for DEMO-1" in out
    assert "## Story 1:" in out
    assert "Acceptance criteria:" in out
    assert "## Open questions" in out
    assert "Nothing has been created in Jira." in out


def test_show_renders_description_links_and_subtasks(monkeypatch, capsys):
    monkeypatch.setattr(cli.Config, "load", staticmethod(lambda: EMPTY))
    cli.main(["show", "DEMO-1", "--demo"])
    out = capsys.readouterr().out

    assert "Self-service account closure" in out
    assert "--- Description ---" in out
    assert "--- Linked issues (1) ---" in out
    assert "--- Existing subtasks (1) ---" in out


def test_an_unknown_demo_key_lists_the_available_ones(monkeypatch, capsys):
    monkeypatch.setattr(cli.Config, "load", staticmethod(lambda: EMPTY))

    assert cli.main(["show", "NOPE-1", "--demo"]) == 1
    out = capsys.readouterr().out
    assert "DEMO-1" in out and "DEMO-7" in out


def test_demo_keys_are_case_insensitive():
    assert get_demo_issue("demo-1") is not None
    assert get_demo_stories("demo-7") is not None


# --- the sample data itself ------------------------------------------------


def test_the_sample_set_has_an_epic_and_a_change_request():
    types = {issue["type"] for issue in DEMO_ISSUES.values()}
    assert "Epic" in types
    assert "Change Request" in types


def test_every_sample_issue_has_matching_suggestions():
    assert set(DEMO_ISSUES) == set(DEMO_STORIES)


def test_sample_issues_match_the_shape_get_context_returns():
    """Demo data flows through the same rendering code as real Jira data."""
    required = {
        "key", "summary", "type", "status", "project", "priority",
        "labels", "description", "links", "subtasks", "url",
    }
    for key, issue in DEMO_ISSUES.items():
        assert required <= set(issue), f"{key} is missing {required - set(issue)}"
        assert issue["description"].strip(), f"{key} needs a description"


def test_sample_suggestions_match_the_shape_the_model_returns():
    for key, result in DEMO_STORIES.items():
        assert result["stories"], f"{key} needs at least one story"
        assert isinstance(result["open_questions"], list)
        for story in result["stories"]:
            assert {"summary", "user_story", "details", "acceptance_criteria"} <= set(story)
            assert story["acceptance_criteria"], f"{key}: every story needs criteria"
            assert story["user_story"].lower().startswith("as a")


def test_sample_data_cannot_be_mistaken_for_the_real_site():
    for issue in DEMO_ISSUES.values():
        assert issue["key"].startswith("DEMO-")
        assert "scientificgames" not in issue["url"]
