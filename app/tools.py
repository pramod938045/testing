"""Jira operations exposed to Claude as tools.

Each entry pairs a JSON-schema tool definition (what Claude sees) with an async
handler that calls `JiraClient`. `strict: True` + `additionalProperties: False`
keep the arguments schema-valid, so handlers can trust their inputs.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from .jira_client import JiraClient, JiraError

Handler = Callable[[JiraClient, dict[str, Any]], Awaitable[Any]]

# Tools that change something in Jira. Disabled entirely when
# JIRA_ALLOW_WRITES=false, and the system prompt requires confirmation first.
WRITE_TOOLS = {
    "create_issue",
    "update_issue",
    "transition_issue",
    "add_comment",
    "log_work",
}


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "search_issues",
        "description": (
            "Search Jira issues with JQL. Use this for any 'find/list/how many' question. "
            "Useful JQL: assignee = currentUser(), sprint in openSprints(), "
            'statusCategory != Done, project = "ABC", updated >= -7d, ORDER BY updated DESC.'
        ),
        "input_schema": _schema(
            {
                "jql": {"type": "string", "description": "A valid JQL query."},
                "max_results": {
                    "type": "integer",
                    "description": "How many issues to return (1-100). Default 25.",
                },
                "next_page_token": {
                    "type": "string",
                    "description": "Token from a previous search to fetch the next page.",
                },
            },
            ["jql"],
        ),
    },
    {
        "name": "get_issue",
        "description": "Get the full detail of one issue, including its description and subtasks.",
        "input_schema": _schema(
            {"key": {"type": "string", "description": "Issue key, e.g. ABC-123."}}, ["key"]
        ),
    },
    {
        "name": "get_comments",
        "description": "Read the most recent comments on an issue (newest first).",
        "input_schema": _schema(
            {
                "key": {"type": "string", "description": "Issue key, e.g. ABC-123."},
                "limit": {"type": "integer", "description": "How many comments. Default 20."},
            },
            ["key"],
        ),
    },
    {
        "name": "list_projects",
        "description": "List/search Jira projects the account can see. Use to resolve a project name to its key.",
        "input_schema": _schema(
            {"query": {"type": "string", "description": "Optional name or key fragment to filter by."}},
            [],
        ),
    },
    {
        "name": "find_user",
        "description": (
            "Look up Jira users by name or email to get an account_id. "
            "Required before assigning an issue to someone."
        ),
        "input_schema": _schema(
            {"query": {"type": "string", "description": "Display name or email fragment."}}, ["query"]
        ),
    },
    {
        "name": "list_issue_types",
        "description": "List the issue types available in a project (Task, Bug, Story, ...). Check before creating.",
        "input_schema": _schema(
            {"project_key": {"type": "string", "description": "Project key, e.g. ABC."}}, ["project_key"]
        ),
    },
    {
        "name": "list_boards",
        "description": "List agile boards, optionally filtered to one project. Needed to reach sprints.",
        "input_schema": _schema(
            {"project_key": {"type": "string", "description": "Optional project key to filter by."}}, []
        ),
    },
    {
        "name": "list_sprints",
        "description": "List sprints on a board.",
        "input_schema": _schema(
            {
                "board_id": {"type": "integer", "description": "Board id from list_boards."},
                "state": {
                    "type": "string",
                    "enum": ["active", "future", "closed", "all"],
                    "description": "Sprint state filter. Default 'active'.",
                },
            },
            ["board_id"],
        ),
    },
    {
        "name": "sprint_report",
        "description": (
            "Summarise a sprint: every issue plus counts grouped by status and by assignee. "
            "Use for standup digests, sprint status and blocker reports."
        ),
        "input_schema": _schema(
            {"sprint_id": {"type": "integer", "description": "Sprint id from list_sprints."}}, ["sprint_id"]
        ),
    },
    {
        "name": "create_issue",
        "description": "Create a new Jira issue. Confirm the details with the user before calling this.",
        "input_schema": _schema(
            {
                "project_key": {"type": "string", "description": "Project key, e.g. ABC."},
                "summary": {"type": "string", "description": "Issue title."},
                "issue_type": {"type": "string", "description": "Task, Bug, Story, ... Default 'Task'."},
                "description": {"type": "string", "description": "Plain-text body; newlines are preserved."},
                "assignee_account_id": {"type": "string", "description": "Account id from find_user."},
                "priority": {"type": "string", "description": "Priority name, e.g. High."},
                "labels": {"type": "array", "items": {"type": "string"}, "description": "Labels to set."},
                "parent_key": {
                    "type": "string",
                    "description": "Parent issue key (for a subtask, or the epic for a story).",
                },
                "due_date": {"type": "string", "description": "Due date as YYYY-MM-DD."},
            },
            ["project_key", "summary"],
        ),
    },
    {
        "name": "update_issue",
        "description": (
            "Update fields on an existing issue (summary, description, assignee, priority, labels, due date). "
            "Only pass the fields that should change. Confirm with the user first. "
            "Use transition_issue to change status."
        ),
        "input_schema": _schema(
            {
                "key": {"type": "string", "description": "Issue key, e.g. ABC-123."},
                "summary": {"type": "string"},
                "description": {"type": "string"},
                "assignee_account_id": {
                    "type": "string",
                    "description": "Account id from find_user; empty string unassigns.",
                },
                "priority": {"type": "string"},
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Replaces the whole label list.",
                },
                "due_date": {"type": "string", "description": "YYYY-MM-DD."},
            },
            ["key"],
        ),
    },
    {
        "name": "list_transitions",
        "description": "List the statuses an issue can move to right now.",
        "input_schema": _schema({"key": {"type": "string"}}, ["key"]),
    },
    {
        "name": "transition_issue",
        "description": (
            "Move an issue to another status (e.g. 'In Progress', 'Done'). "
            "Matches on the transition or target status name. Confirm with the user first."
        ),
        "input_schema": _schema(
            {
                "key": {"type": "string", "description": "Issue key, e.g. ABC-123."},
                "to_status": {"type": "string", "description": "Target status or transition name."},
            },
            ["key", "to_status"],
        ),
    },
    {
        "name": "add_comment",
        "description": "Add a comment to an issue. Show the user the exact text and confirm before calling.",
        "input_schema": _schema(
            {
                "key": {"type": "string", "description": "Issue key, e.g. ABC-123."},
                "body": {"type": "string", "description": "Comment text."},
            },
            ["key", "body"],
        ),
    },
    {
        "name": "log_work",
        "description": "Log time against an issue. Confirm with the user first.",
        "input_schema": _schema(
            {
                "key": {"type": "string", "description": "Issue key, e.g. ABC-123."},
                "time_spent": {"type": "string", "description": "Jira duration, e.g. '3h 30m', '1d'."},
                "comment": {"type": "string", "description": "Optional worklog note."},
                "started": {
                    "type": "string",
                    "description": "Start time as 2026-09-06T10:00:00.000+0000. Defaults to now.",
                },
            },
            ["key", "time_spent"],
        ),
    },
]


async def _search(client: JiraClient, args: dict[str, Any]) -> Any:
    return await client.search(
        jql=args["jql"],
        max_results=min(int(args.get("max_results") or 25), 100),
        next_page_token=args.get("next_page_token"),
    )


HANDLERS: dict[str, Handler] = {
    "search_issues": _search,
    "get_issue": lambda c, a: c.get_issue(a["key"]),
    "get_comments": lambda c, a: c.get_comments(a["key"], int(a.get("limit") or 20)),
    "list_projects": lambda c, a: c.list_projects(a.get("query")),
    "find_user": lambda c, a: c.find_user(a["query"]),
    "list_issue_types": lambda c, a: c.list_issue_types(a["project_key"]),
    "list_boards": lambda c, a: c.list_boards(a.get("project_key")),
    "list_sprints": lambda c, a: c.list_sprints(int(a["board_id"]), a.get("state") or "active"),
    "sprint_report": lambda c, a: c.sprint_report(int(a["sprint_id"])),
    "create_issue": lambda c, a: c.create_issue(
        project_key=a["project_key"],
        summary=a["summary"],
        issue_type=a.get("issue_type") or "Task",
        description=a.get("description"),
        assignee_account_id=a.get("assignee_account_id"),
        priority=a.get("priority"),
        labels=a.get("labels"),
        parent_key=a.get("parent_key"),
        due_date=a.get("due_date"),
    ),
    "update_issue": lambda c, a: c.update_issue(
        key=a["key"],
        summary=a.get("summary"),
        description=a.get("description"),
        assignee_account_id=a.get("assignee_account_id"),
        priority=a.get("priority"),
        labels=a.get("labels"),
        due_date=a.get("due_date"),
    ),
    "list_transitions": lambda c, a: c.list_transitions(a["key"]),
    "transition_issue": lambda c, a: c.transition_issue(a["key"], a["to_status"]),
    "add_comment": lambda c, a: c.add_comment(a["key"], a["body"]),
    "log_work": lambda c, a: c.log_work(
        a["key"], a["time_spent"], a.get("comment"), a.get("started")
    ),
}


def tool_definitions(allow_writes: bool = True) -> list[dict[str, Any]]:
    """The tool list to send to Claude, with `strict` schema validation on."""
    return [
        {**spec, "strict": True}
        for spec in TOOL_SPECS
        if allow_writes or spec["name"] not in WRITE_TOOLS
    ]


async def execute_tool(
    client: JiraClient, name: str, args: dict[str, Any], allow_writes: bool = True
) -> tuple[Any, bool]:
    """Run one tool call. Returns `(result, is_error)`.

    Errors are returned rather than raised so the loop can hand them back to
    Claude as `tool_result` blocks with `is_error: true` — the model then
    explains or retries instead of the whole request failing.
    """
    if name in WRITE_TOOLS and not allow_writes:
        return (
            f"Refused: '{name}' modifies Jira, but this bot is running in read-only mode "
            "(JIRA_ALLOW_WRITES=false). Tell the user you cannot make changes.",
            True,
        )
    handler = HANDLERS.get(name)
    if handler is None:
        return f"Unknown tool '{name}'.", True

    try:
        return await handler(client, args), False
    except JiraError as exc:
        return f"Jira error: {exc.message}", True
    except (KeyError, ValueError, TypeError) as exc:
        return f"Invalid arguments for '{name}': {exc}", True
