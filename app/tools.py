"""Jira operations exposed to Claude as tools. All of them read; none write.

Each entry pairs a JSON-schema tool definition (what Claude sees) with an async
handler that calls `JiraClient`. `strict: True` + `additionalProperties: False`
keep the arguments schema-valid, so handlers can trust their inputs.

There is deliberately no tool that creates, edits, transitions, comments on or
deletes anything: the model cannot call what it is not given, and `JiraClient`
would refuse the request anyway.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from .jira_client import JiraClient, JiraError

Handler = Callable[[JiraClient, dict[str, Any]], Awaitable[Any]]


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
        "description": (
            "Get the full detail of one issue, including its description and subtasks. "
            "Use this when the user asks what an issue or story says."
        ),
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
        "description": "Look up Jira users by name or email, to resolve who someone is.",
        "input_schema": _schema(
            {"query": {"type": "string", "description": "Display name or email fragment."}}, ["query"]
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
        "name": "list_transitions",
        "description": (
            "List the statuses an issue could move to. This only reports what is possible — "
            "you cannot perform a transition."
        ),
        "input_schema": _schema({"key": {"type": "string"}}, ["key"]),
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
    "list_boards": lambda c, a: c.list_boards(a.get("project_key")),
    "list_sprints": lambda c, a: c.list_sprints(int(a["board_id"]), a.get("state") or "active"),
    "sprint_report": lambda c, a: c.sprint_report(int(a["sprint_id"])),
    "list_transitions": lambda c, a: c.list_transitions(a["key"]),
}


def tool_definitions() -> list[dict[str, Any]]:
    """The tool list to send to Claude, with `strict` schema validation on."""
    return [{**spec, "strict": True} for spec in TOOL_SPECS]


async def execute_tool(client: JiraClient, name: str, args: dict[str, Any]) -> tuple[Any, bool]:
    """Run one tool call. Returns `(result, is_error)`.

    Errors are returned rather than raised so the loop can hand them back to
    Claude as `tool_result` blocks with `is_error: true` — the model then
    explains or retries instead of the whole request failing.
    """
    handler = HANDLERS.get(name)
    if handler is None:
        return f"Unknown tool '{name}'.", True

    try:
        return await handler(client, args), False
    except JiraError as exc:
        return f"Jira error: {exc.message}", True
    except (KeyError, ValueError, TypeError) as exc:
        return f"Invalid arguments for '{name}': {exc}", True
