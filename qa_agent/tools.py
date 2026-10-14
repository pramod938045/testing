"""TestRail operations exposed to Claude as tools.

The Jira tools are reused unchanged from `app.tools` (all read-only). Every
TestRail tool here reads, except `create_test_cases`, which the agent only
runs after a person approves the exact cases (see `QAAgent._create_cases`).
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from app.tools import _schema

from .testrail_client import TestRailClient, TestRailError

Handler = Callable[[TestRailClient, dict[str, Any]], Awaitable[Any]]

CREATE_TOOL = "create_test_cases"

_PROJECT_ID = {"type": "integer", "description": "TestRail project id from testrail_list_projects."}
_SUITE_ID = {"type": "integer", "description": "Suite id, needed for projects with multiple suites."}

TESTRAIL_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "testrail_list_projects",
        "description": "List active TestRail projects. Use to resolve a project name to its id.",
        "input_schema": _schema({}, []),
    },
    {
        "name": "testrail_list_suites",
        "description": "List the test suites in a TestRail project.",
        "input_schema": _schema({"project_id": _PROJECT_ID}, ["project_id"]),
    },
    {
        "name": "testrail_list_sections",
        "description": "List the sections (folders) in a project or suite. New cases are added to a section.",
        "input_schema": _schema({"project_id": _PROJECT_ID, "suite_id": _SUITE_ID}, ["project_id"]),
    },
    {
        "name": "testrail_find_cases",
        "description": (
            "List test cases in a project. Pass `jira_key` to get only the cases whose References "
            "field names that Jira issue — this is how Jira stories are linked to TestRail coverage."
        ),
        "input_schema": _schema(
            {
                "project_id": _PROJECT_ID,
                "suite_id": _SUITE_ID,
                "section_id": {"type": "integer", "description": "Optional section to limit to."},
                "jira_key": {"type": "string", "description": "Jira issue key, e.g. ABC-123."},
            },
            ["project_id"],
        ),
    },
    {
        "name": "testrail_get_case",
        "description": "Get one test case in full: preconditions, steps and expected results.",
        "input_schema": _schema({"case_id": {"type": "integer"}}, ["case_id"]),
    },
    {
        "name": "testrail_list_runs",
        "description": "List test runs in a project with their passed/failed/blocked/untested counts.",
        "input_schema": _schema(
            {
                "project_id": _PROJECT_ID,
                "is_completed": {"type": "boolean", "description": "true for closed runs, false for open ones."},
            },
            ["project_id"],
        ),
    },
    {
        "name": "testrail_get_run_tests",
        "description": "Every test in a run with its current status (Passed, Failed, ...) and counts by status.",
        "input_schema": _schema({"run_id": {"type": "integer"}}, ["run_id"]),
    },
    {
        "name": CREATE_TOOL,
        "description": (
            "Add new test cases to a TestRail section, linked to a Jira issue via References. "
            "The user is shown the exact cases and must approve them before anything is created; "
            "if they decline, nothing is written. Check existing coverage with testrail_find_cases first."
        ),
        "input_schema": _schema(
            {
                "section_id": {"type": "integer", "description": "Section to add the cases to."},
                "jira_key": {"type": "string", "description": "Jira issue the cases verify, e.g. ABC-123."},
                "cases": {
                    "type": "array",
                    "description": "The test cases to create.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "preconditions": {"type": "string"},
                            "steps": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "action": {"type": "string"},
                                        "expected": {"type": "string"},
                                    },
                                    "required": ["action", "expected"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["title", "steps"],
                        "additionalProperties": False,
                    },
                },
            },
            ["section_id", "jira_key", "cases"],
        ),
    },
]


def _optional_int(args: dict[str, Any], name: str) -> int | None:
    return int(args[name]) if args.get(name) is not None else None


TESTRAIL_HANDLERS: dict[str, Handler] = {
    "testrail_list_projects": lambda c, a: c.list_projects(),
    "testrail_list_suites": lambda c, a: c.list_suites(int(a["project_id"])),
    "testrail_list_sections": lambda c, a: c.list_sections(int(a["project_id"]), _optional_int(a, "suite_id")),
    "testrail_find_cases": lambda c, a: c.find_cases(
        int(a["project_id"]),
        suite_id=_optional_int(a, "suite_id"),
        section_id=_optional_int(a, "section_id"),
        ref=a.get("jira_key"),
    ),
    "testrail_get_case": lambda c, a: c.get_case(int(a["case_id"])),
    "testrail_list_runs": lambda c, a: c.list_runs(int(a["project_id"]), a.get("is_completed")),
    "testrail_get_run_tests": lambda c, a: c.get_run_tests(int(a["run_id"])),
}


async def execute_testrail_tool(client: TestRailClient, name: str, args: dict[str, Any]) -> tuple[Any, bool]:
    """Run one read-only TestRail tool. Returns `(result, is_error)`, like `app.tools.execute_tool`."""
    handler = TESTRAIL_HANDLERS.get(name)
    if handler is None:
        return f"Unknown tool '{name}'.", True
    try:
        return await handler(client, args), False
    except TestRailError as exc:
        return f"TestRail error: {exc.message}", True
    except (KeyError, ValueError, TypeError) as exc:
        return f"Invalid arguments for '{name}': {exc}", True
