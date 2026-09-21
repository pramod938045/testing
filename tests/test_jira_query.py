"""English -> JQL, without the AI.

The important half is what it refuses. A follow-up like "who is it assigned
to?" must not become a search across the whole site, so anything the rules do
not clearly recognise returns None and the caller answers from the open issue.
"""

import pytest

from app.jira_query import build, epic_children


def jql(text, project=""):
    query = build(text, project)
    return query.jql if query else None


# --- what it refuses --------------------------------------------------------


@pytest.mark.parametrize("question", [
    "who is it assigned to?",
    "what is the status?",
    "show the description",
    "show the comments",
    "hello there",
    "what does this mean",
    "any linked issues?",
    "thanks",
])
def test_a_follow_up_is_not_turned_into_a_search(question):
    assert build(question) is None


# --- what it builds ---------------------------------------------------------


def test_assigned_to_me_and_not_done():
    query = build("what is assigned to me and not done?")

    assert "assignee = currentUser()" in query.jql
    assert "statusCategory != Done" in query.jql
    assert query.jql.endswith("ORDER BY updated DESC")
    assert "assigned to you" in query.label and "not done" in query.label


def test_the_undone_fallback_avoids_status_category():
    query = build("my open bugs")

    assert "resolution = EMPTY" in query.candidates[1]
    assert "statusCategory" not in query.candidates[1]


@pytest.mark.parametrize("text,expected", [
    ("show bugs", 'issuetype = "Bug"'),
    ("list the stories", 'issuetype = "Story"'),
    ("open epics", 'issuetype = "Epic"'),
    ("show defects", 'issuetype = "Bug"'),
])
def test_issue_types_are_recognised(text, expected):
    assert expected in jql(text)


@pytest.mark.parametrize("text,expected", [
    ("bugs updated in the last 7 days", "-7d"),
    ("bugs updated in the last 2 weeks", "-14d"),
    ("bugs updated in the last month", "-30d"),
    ("bugs updated today", "-1d"),
    ("bugs updated this week", "-7d"),
])
def test_time_windows_become_days(text, expected):
    assert f"updated >= {expected}" in jql(text)


def test_a_named_project_scopes_the_query():
    assert "project = DFE" in jql("show me open bugs in DFE")


def test_the_default_project_is_used_when_none_is_named():
    assert "project = UPAMCORE" in jql("show me open bugs", project="UPAMCORE")


def test_a_named_project_beats_the_default():
    assert "project = DFE" in jql("open bugs in DFE", project="UPAMCORE")


def test_unassigned_work():
    assert "assignee is EMPTY" in jql("what is unassigned and not done?")


def test_done_and_not_done_are_not_confused():
    assert "statusCategory != Done" in jql("what is not done?")
    assert "statusCategory = Done" in jql("what was completed this week?")


# --- the parts that vary between Jira sites ---------------------------------


def test_blocked_offers_every_meaning_best_first():
    query = build("what is blocked right now?")

    assert query.candidates[0].startswith('issueLinkType = "is blocked by"')
    assert any("status = Blocked" in candidate for candidate in query.candidates)
    assert any("labels in" in candidate for candidate in query.candidates)
    assert len(query.candidates) == 5


def test_epic_children_try_epic_link_then_parent():
    query = epic_children("UPAMCORE-30000")

    assert query.candidates[0].startswith('"Epic Link" = UPAMCORE-30000')
    assert query.candidates[1].startswith("parent = UPAMCORE-30000")


def test_a_bare_open_does_not_filter_by_status():
    """'open the description' must not become a status filter."""
    assert build("open the description") is None
