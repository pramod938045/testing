"""Cross-issue questions in Real Jira mode: CRs, epic children, JQL, sprints.

The bug these cover: the follow-up table matched on substrings with no notion
of scope, so "what is blocked right now?" hit the "block" rule and returned the
*links of the last issue* — a confident answer about the wrong ticket. Anything
asking about more than one issue must now reach Jira, or say it cannot.
"""

import httpx
import pytest

from app.jira_client import JiraClient
from app.jira_direct import JiraDirectAgent

SITE = "https://jira.scigames.at"

STORY = {
    "key": "UPAMCORE-30727",
    "fields": {
        "summary": "Route fund collection feedback to the Java service",
        "issuetype": {"name": "Story"},
        "status": {"name": "In Progress", "statusCategory": {"name": "In Progress"}},
        "project": {"name": "UPAM Core", "key": "UPAMCORE"},
        "assignee": {"displayName": "Ana Silva"},
        "description": "Proposed implementation.",
        "parent": {"key": "UPAMCORE-30000"},
        "issuelinks": [
            {
                "type": {"outward": "is implemented by", "inward": "implements"},
                "outwardIssue": {
                    "key": "UPAMCORE-31765",
                    "fields": {"summary": "subscriptions-funding:17.35.0",
                               "status": {"name": "Built"}},
                },
            },
            {
                "type": {"outward": "relates to", "inward": "relates to"},
                "outwardIssue": {
                    "key": "CHG-4412",
                    "fields": {"summary": "Deploy funding service 17.35.0",
                               "status": {"name": "Approved"}},
                },
            },
        ],
        "subtasks": [],
    },
}


def issue_row(key, summary, type_name, status="Open", assignee="Ana Silva"):
    """A row as `search` returns it, via simplify_issue."""
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "issuetype": {"name": type_name},
            "status": {"name": status, "statusCategory": {"name": "In Progress"}},
            "assignee": {"displayName": assignee},
            "project": {"key": "UPAMCORE"},
        },
    }


class Jira:
    """Records every request and replies from a routing table."""

    def __init__(self, routes, issue=STORY):
        self.routes = routes
        self.issue = issue
        self.searches: list[str] = []
        self.paths: list[str] = []

    def handler(self, request):
        self.paths.append(request.url.path)
        if request.url.path.endswith("/search"):
            import json

            jql = json.loads(request.content)["jql"]
            self.searches.append(jql)
            for fragment, response in self.routes.items():
                if fragment in jql:
                    return response(jql) if callable(response) else response
            return httpx.Response(200, json={"issues": [], "total": 0})
        return httpx.Response(200, json=self.issue)

    def agent(self, **kwargs):
        client = JiraClient(SITE, "", "pat")
        client._client = httpx.AsyncClient(
            transport=httpx.MockTransport(self.handler), base_url=SITE
        )
        return JiraDirectAgent(jira=client, **kwargs)


def issues(*rows, total=None):
    payload = {"issues": list(rows)}
    payload["total"] = total if total is not None else len(rows)
    return httpx.Response(200, json=payload)


def rejected(message="Field 'statusCategory' does not exist"):
    return httpx.Response(400, json={"errorMessages": [message]})


# --- the wrong-answer bug ---------------------------------------------------


@pytest.mark.parametrize("question,expect_call", [
    ("what is assigned to me and not done?", "search"),
    ("what is blocked right now?", "search"),
    ("show bugs updated in the last 7 days", "search"),
    ("summarise the current sprint", "list_boards"),
])
async def test_a_cross_issue_question_reaches_jira_instead_of_the_open_issue(question, expect_call):
    """These used to match 'assign' / 'block' / 'summar' on the open issue and
    answer from it without contacting Jira at all — a confident wrong answer.

    Asserting on the *call made* is the check that matters: an assertion about
    the reply text passes even when the answer came from the wrong issue."""
    jira = Agile([BOARD], [SPRINT], [])
    agent = jira.agent()
    await agent.chat("UPAMCORE-30727")
    jira.searches.clear()

    result = await agent.chat(question)

    assert [call.name for call in result.tool_calls], f"{question!r} contacted no Jira endpoint"
    assert result.tool_calls[0].name == expect_call
    assert "UPAMCORE-30727** — assignee" not in result.reply


# --- 2. the CR ---------------------------------------------------------------


async def test_the_cr_is_found_by_issue_type_not_by_the_link(capsys):
    """Two links; only the one Jira types as a Change Request is the CR."""
    jira = Jira({
        "key in": issues(
            issue_row("UPAMCORE-31765", "subscriptions-funding:17.35.0", "Build"),
            issue_row("CHG-4412", "Deploy funding service 17.35.0", "Change Request",
                      status="Approved"),
            issue_row("UPAMCORE-30000", "Funding epic", "Epic"),
        )
    })
    agent = jira.agent()
    await agent.chat("UPAMCORE-30727")

    result = await agent.chat("provide the CR ticket for this story")

    assert "CHG-4412" in result.reply
    assert "Change Request for UPAMCORE-30727" in result.reply
    assert "relates to" in result.reply, "the relationship is explained"
    # The build link is a neighbour, not a CR.
    assert "**UPAMCORE-31765**" not in result.reply


async def test_every_related_issue_is_checked_including_parent():
    jira = Jira({"key in": issues()})
    agent = jira.agent()
    await agent.chat("UPAMCORE-30727")
    await agent.chat("what is the CR for this story?")

    batch = next(jql for jql in jira.searches if jql.startswith("key in"))
    assert "UPAMCORE-31765" in batch and "CHG-4412" in batch
    assert "UPAMCORE-30000" in batch, "the parent is a candidate too"


async def test_no_cr_says_so_and_lists_what_was_checked():
    jira = Jira({
        "key in": issues(
            issue_row("UPAMCORE-31765", "subscriptions-funding:17.35.0", "Build"),
            issue_row("CHG-4412", "Deploy funding service", "Task"),
            issue_row("UPAMCORE-30000", "Funding epic", "Epic"),
        )
    })
    agent = jira.agent()
    await agent.chat("UPAMCORE-30727")

    result = await agent.chat("provide the CR ticket for this story")

    assert "No Change Request is linked to UPAMCORE-30727" in result.reply
    assert "_Build_" in result.reply and "_Epic_" in result.reply, "shows what it checked"


async def test_multiple_crs_are_all_shown_with_their_relationships():
    jira = Jira({
        "key in": issues(
            issue_row("UPAMCORE-31765", "First change", "Change Request"),
            issue_row("CHG-4412", "Second change", "Change Request"),
            issue_row("UPAMCORE-30000", "Funding epic", "Epic"),
        )
    })
    agent = jira.agent()
    await agent.chat("UPAMCORE-30727")

    result = await agent.chat("provide the CR ticket for this story")

    assert "2 possible Change Requests" in result.reply
    assert "UPAMCORE-31765" in result.reply and "CHG-4412" in result.reply
    assert "is implemented by" in result.reply and "relates to" in result.reply


async def test_an_issue_with_no_neighbours_cannot_have_a_cr():
    bare = {"key": "UPAMCORE-1", "fields": {"summary": "Alone", "issuetype": {"name": "Story"},
                                            "status": {"name": "Open"}, "issuelinks": [],
                                            "subtasks": []}}
    jira = Jira({}, issue=bare)
    agent = jira.agent()
    await agent.chat("UPAMCORE-1")

    result = await agent.chat("provide the CR ticket for this story")

    assert "no parent, no linked issues and no subtasks" in result.reply
    assert not [jql for jql in jira.searches if jql.startswith("key in")], "nothing to look up"


async def test_the_cr_question_can_name_its_own_issue():
    jira = Jira({"key in": issues(
        issue_row("CHG-4412", "Deploy", "Change Request"),
        issue_row("UPAMCORE-31765", "Build", "Build"),
        issue_row("UPAMCORE-30000", "Epic", "Epic"),
    )})
    agent = jira.agent()

    result = await agent.chat("what's the change request for UPAMCORE-30727?")

    assert "CHG-4412" in result.reply
    assert "/rest/api/2/issue/UPAMCORE-30727" in jira.paths


# --- 3. epic children --------------------------------------------------------


async def test_epic_children_come_from_jira():
    jira = Jira({"Epic Link": issues(
        issue_row("UPAMCORE-30727", "Route feedback", "Story", assignee="Ana Silva"),
        issue_row("UPAMCORE-30801", "Add metrics", "Story", assignee="Kim Ross"),
    )})
    agent = jira.agent()

    result = await agent.chat("what stories are under epic UPAMCORE-30000?")

    assert '"Epic Link" = UPAMCORE-30000' in jira.searches[0]
    assert "UPAMCORE-30727" in result.reply and "UPAMCORE-30801" in result.reply
    assert "Kim Ross" in result.reply, "assignees are shown"
    assert "Route feedback" in result.reply, "summaries are shown"


async def test_epic_children_fall_back_to_parent_when_epic_link_is_unsupported():
    """Team-managed and newer sites have no "Epic Link" field."""
    jira = Jira({
        "Epic Link": rejected('Field \'Epic Link\' does not exist'),
        "parent =": issues(issue_row("UPAMCORE-30727", "Route feedback", "Story")),
    })
    agent = jira.agent()

    result = await agent.chat("what stories are under epic UPAMCORE-30000?")

    assert "UPAMCORE-30727" in result.reply
    assert any("parent = UPAMCORE-30000" in jql for jql in jira.searches)


async def test_asking_about_the_epic_while_a_story_is_open_uses_its_parent():
    """A story has no children of its own; "this epic" means the one above it."""
    jira = Jira({"Epic Link": issues(issue_row("UPAMCORE-30801", "Add metrics", "Story"))})
    agent = jira.agent()
    await agent.chat("UPAMCORE-30727")

    result = await agent.chat("what stories are under this epic?")

    assert "is a _Story_, so I used its parent **UPAMCORE-30000**" in result.reply
    assert '"Epic Link" = UPAMCORE-30000' in jira.searches[0]
    assert "UPAMCORE-30801" in result.reply


async def test_an_epic_with_no_children_says_so():
    jira = Jira({})
    agent = jira.agent()

    result = await agent.chat("what stories are under UPAMCORE-30000?")

    assert "No issues under **UPAMCORE-30000**" in result.reply
    assert "Nothing in Jira lists **UPAMCORE-30000** as its epic" in result.reply


# --- 4. JQL search -----------------------------------------------------------


async def test_assigned_to_me_and_not_done():
    jira = Jira({"currentUser": issues(issue_row("UPAMCORE-30727", "Route feedback", "Story"))})
    agent = jira.agent()

    result = await agent.chat("what is assigned to me and not done?")

    assert "assignee = currentUser()" in jira.searches[0]
    assert "statusCategory != Done" in jira.searches[0]
    assert "UPAMCORE-30727" in result.reply
    assert "JQL:" in result.reply, "the query is shown"


async def test_status_category_falls_back_to_resolution():
    jira = Jira({
        "statusCategory": rejected(),
        "resolution = EMPTY": issues(issue_row("UPAMCORE-30727", "Route feedback", "Story")),
    })
    agent = jira.agent()

    result = await agent.chat("what is assigned to me and not done?")

    assert "UPAMCORE-30727" in result.reply
    assert any("resolution = EMPTY" in jql for jql in jira.searches)


async def test_bugs_updated_in_the_last_7_days():
    jira = Jira({"issuetype": issues(issue_row("UPAMCORE-99", "Crash", "Bug"))})
    agent = jira.agent()

    result = await agent.chat("show bugs updated in the last 7 days")

    jql = jira.searches[0]
    assert 'issuetype = "Bug"' in jql
    assert "updated >= -7d" in jql
    assert "UPAMCORE-99" in result.reply


async def test_a_two_week_window_becomes_days():
    jira = Jira({"updated": issues()})
    agent = jira.agent()
    await agent.chat("show bugs updated in the last 2 weeks")

    assert "updated >= -14d" in jira.searches[0]


async def test_blocked_tries_each_meaning_until_one_works():
    jira = Jira({
        "issueLinkType": rejected("The value 'is blocked by' does not exist"),
        "status = Blocked": issues(issue_row("UPAMCORE-30801", "Stuck", "Story",
                                             status="Blocked")),
    })
    agent = jira.agent()

    result = await agent.chat("what is blocked right now?")

    assert "UPAMCORE-30801" in result.reply
    assert jira.searches[0].startswith('issueLinkType'), "best guess first"


async def test_an_empty_result_shows_the_query_that_matched_nothing():
    jira = Jira({})
    agent = jira.agent()

    result = await agent.chat("what is assigned to me and not done?")

    assert "No issues" in result.reply
    assert "assignee = currentUser()" in result.reply


async def test_every_rejected_candidate_reports_jiras_own_words():
    jira = Jira({"": rejected("Field 'flagged' does not exist")})
    agent = jira.agent()

    result = await agent.chat("what is blocked right now?")

    assert "Field 'flagged' does not exist" in result.reply
    assert result.tool_calls[0].ok is False


async def test_raw_jql_is_passed_through():
    jira = Jira({"project = DFE": issues(issue_row("DFE-9067", "Payments", "Story"))})
    agent = jira.agent()

    result = await agent.chat("jql: project = DFE AND status = Open")

    assert jira.searches[0] == "project = DFE AND status = Open"
    assert "DFE-9067" in result.reply


async def test_a_default_project_scopes_the_search():
    jira = Jira({"project = UPAMCORE": issues()})
    agent = jira.agent(default_project="UPAMCORE")
    await agent.chat("what is assigned to me and not done?")

    assert "project = UPAMCORE" in jira.searches[0]


# --- 5. sprint ---------------------------------------------------------------


class Agile(Jira):
    def __init__(self, boards, sprints, sprint_issues, sprint_status=200):
        super().__init__({})
        self.boards, self.sprints = boards, sprints
        self.sprint_issues, self.sprint_status = sprint_issues, sprint_status
        self.urls: list[str] = []

    def handler(self, request):
        path = request.url.path
        self.paths.append(path)
        self.urls.append(str(request.url))
        if path.endswith("/board"):
            return httpx.Response(200, json={"values": self.boards})
        if path.endswith("/sprint"):
            return httpx.Response(200, json={"values": self.sprints})
        if "/sprint/" in path and path.endswith("/issue"):
            return httpx.Response(self.sprint_status, json={"issues": self.sprint_issues})
        return super().handler(request)


BOARD = {"id": 12, "name": "UPAM Core Scrum", "type": "scrum",
         "location": {"projectKey": "UPAMCORE"}}
SPRINT = {"id": 445, "name": "UPAM Sprint 63", "state": "active",
          "startDate": "2026-09-01T09:00:00.000Z", "endDate": "2026-09-15T09:00:00.000Z",
          "goal": "Ship funding 17.35"}


async def test_the_sprint_summary_uses_the_real_active_sprint():
    jira = Agile([BOARD], [SPRINT], [
        issue_row("UPAMCORE-30727", "Route feedback", "Story", status="In Progress"),
        issue_row("UPAMCORE-30801", "Add metrics", "Story", status="Done", assignee="Kim Ross"),
    ])
    agent = jira.agent()
    await agent.chat("UPAMCORE-30727")

    result = await agent.chat("summarise the current sprint")

    assert "UPAM Sprint 63" in result.reply
    assert "Ship funding 17.35" in result.reply
    assert "In Progress: 1" in result.reply and "Done: 1" in result.reply
    assert "Kim Ross: 1" in result.reply
    assert "/rest/agile/1.0/sprint/445/issue" in jira.paths


async def test_no_board_explains_rather_than_showing_an_empty_sprint():
    jira = Agile([], [], [])
    agent = jira.agent()
    await agent.chat("UPAMCORE-30727")

    result = await agent.chat("summarise the current sprint")

    assert "no board" in result.reply
    assert "Sprints belong to boards" in result.reply


async def test_no_active_sprint_names_the_boards_checked():
    jira = Agile([BOARD], [], [])
    agent = jira.agent()
    await agent.chat("UPAMCORE-30727")

    result = await agent.chat("summarise the current sprint")

    assert "No **active** sprint" in result.reply
    assert "UPAM Core Scrum" in result.reply
    assert "Kanban" in result.reply


async def test_the_sprint_project_comes_from_a_named_key():
    """The board is looked up for the project the key belongs to."""
    jira = Agile([BOARD], [SPRINT], [])
    agent = jira.agent()

    await agent.chat("summarise the current sprint for UPAMCORE-30727")

    board_request = next(url for url in jira.urls if "/board" in url)
    assert "projectKeyOrId=UPAMCORE" in board_request


async def test_a_sprint_question_with_no_context_asks_which_project():
    jira = Agile([], [], [])
    agent = jira.agent()

    result = await agent.chat("summarise the current sprint")

    assert "Which project" in result.reply
    assert jira.paths == [], "no pointless Jira call"


# --- 6. follow-ups still work ------------------------------------------------


async def test_single_issue_follow_ups_are_unaffected():
    jira = Jira({})
    agent = jira.agent()
    await agent.chat("UPAMCORE-30727")

    assert "In Progress" in (await agent.chat("what is the status?")).reply
    assert "Ana Silva" in (await agent.chat("who is it assigned to?")).reply
    assert jira.searches == [], "follow-ups answer from the issue already fetched"


# --- 8. safety ---------------------------------------------------------------


async def test_nothing_in_this_flow_writes_to_jira():
    methods: list[str] = []

    class Recorder(Jira):
        def handler(self, request):
            methods.append(request.method)
            return super().handler(request)

    jira = Recorder({"key in": issues(), "Epic Link": issues(), "currentUser": issues()})
    agent = jira.agent()
    for message in [
        "UPAMCORE-30727",
        "provide the CR ticket for this story",
        "what stories are under this epic?",
        "what is assigned to me and not done?",
        "jql: project = UPAMCORE",
    ]:
        await agent.chat(message)

    assert set(methods) <= {"GET", "POST"}, "only reads"
    assert all(
        path.endswith("/search") for path, method in zip(jira.paths, methods) if method == "POST"
    ), "POST is only ever search"
