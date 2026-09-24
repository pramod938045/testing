"""An MCP server exposing this project's Jira reading to a Claude Code session.

Run it on a machine that can reach Jira. Claude then reads a ticket by key —
"write manual test cases for UPAMCORE-29249" — instead of being handed pasted
text, and the credential never leaves that machine.

Three layers stand between the model and a change to Jira:

1. `STORYGEN_MCP_ALLOW_WRITES` is unset by default, and the write tools refuse
   outright without it. A model cannot set an environment variable, so this
   one is not persuadable.
2. Write tools default to `dry_run=True`, returning what *would* be posted.
   Writing takes a deliberate `dry_run=False`.
3. They are annotated destructive, so the MCP client asks the human before
   each call and shows the arguments.

Reading uses `storygen.jira.Jira`, which refuses every non-GET request before
it is sent. Writing is confined to posting rendered test cases — there is no
general "edit this issue" tool here on purpose.
"""

from __future__ import annotations

import json
import os
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from .config import Config, ConfigError
from .jira import Jira, JiraError
from .publish import JiraWriter, render_case, render_plain
from .render import epic_document, issue_block
from .testcases import SYSTEM as TEST_CASE_RULES
from .testcases import SCHEMA as TEST_CASE_SCHEMA
from .testcases import build_prompt, render_csv

WRITES_ENABLED = "STORYGEN_MCP_ALLOW_WRITES"

server = MCPServer(
    name="storygen-jira",
    instructions=(
        "Read Jira issues and write manual test cases for them.\n\n"
        "To write test cases for a ticket, call `jira_test_case_brief` and follow "
        "the rules it returns — do not invent requirements the ticket does not "
        "state. To publish them back to Jira, call `jira_publish_test_cases`; it "
        "previews by default and the human must approve the real write."
    ),
)

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=True)
WRITES = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True)


def _config() -> Config:
    config = Config.load()
    config.check_jira()
    return config


def _client() -> Jira:
    config = _config()
    return Jira(
        config.jira_url, config.jira_email, config.jira_token, deployment=config.deployment
    )


def _writes_allowed() -> bool:
    return os.getenv(WRITES_ENABLED, "").strip().lower() in {"1", "true", "yes", "on"}


def _refusal() -> str:
    return (
        f"Writing to Jira is switched off. This server only writes when "
        f"{WRITES_ENABLED}=1 is set in its environment, and it is not set.\n\n"
        "This is deliberate and cannot be changed from here. Ask the person "
        "running the server to set it and restart, or give them the rendered "
        "test cases to paste in themselves."
    )


def _guard(call):
    """Run a tool body, turning expected failures into readable text.

    An MCP client shows the model whatever comes back, so a traceback helps
    nobody; a sentence saying which setting is wrong does.
    """
    try:
        return call()
    except ConfigError as exc:
        return f"Configuration problem: {exc}"
    except JiraError as exc:
        return f"Jira error: {exc}"


# ---------------------------------------------------------------- reading


@server.tool(
    description=(
        "Read one Jira issue in full: summary, status, description, subtasks and "
        "linked issues. Use this when given an issue key or a Jira URL."
    ),
    annotations=READ_ONLY,
)
def jira_issue(key: str) -> str:
    """`key` is an issue key such as UPAMCORE-29249."""

    def run() -> str:
        jira = _client()
        try:
            return "\n".join(issue_block(jira.get_context(key.strip().upper())))
        finally:
            jira.close()

    return _guard(run)


@server.tool(
    description=(
        "Read an epic and every story under it: the epic in full, a table of its "
        "children, then each story's own description. Set include_descriptions "
        "to false for just the table."
    ),
    annotations=READ_ONLY,
)
def jira_epic(key: str, include_descriptions: bool = True) -> str:
    """`key` is the epic's key."""

    def run() -> str:
        jira = _client()
        try:
            epic_key = key.strip().upper()
            parent = jira.get_context(epic_key)
            children, jql = jira.find_children(epic_key)
            full = (
                [jira.get_context(child["key"]) for child in children]
                if include_descriptions
                else []
            )
            return epic_document(parent, children, jql, full)
        finally:
            jira.close()

    return _guard(run)


@server.tool(
    description="Find Epics and Change Requests whose summary or description matches some text.",
    annotations=READ_ONLY,
)
def jira_find(query: str) -> str:
    """`query` is free text, or an issue key to look up directly."""

    def run() -> str:
        jira = _client()
        try:
            issues, type_filtered = jira.find_parents(query)
            if not issues:
                return f"No Epics or Change Requests match {query!r}."
            lines = [f"{len(issues)} match(es) for {query!r}:", ""]
            if not type_filtered:
                lines += ["_This site has no Epic/Change Request types — searched all types._", ""]
            lines += ["| Key | Type | Status | Summary |", "| --- | --- | --- | --- |"]
            for issue in issues:
                fields = issue.get("fields", {})
                lines.append(
                    f"| {issue['key']} "
                    f"| {(fields.get('issuetype') or {}).get('name', '?')} "
                    f"| {(fields.get('status') or {}).get('name', '?')} "
                    f"| {fields.get('summary', '')} |"
                )
            return "\n".join(lines)
        finally:
            jira.close()

    return _guard(run)


@server.tool(
    description=(
        "Everything needed to write manual test cases for an issue: the ticket's "
        "content, the rules to follow, and the JSON shape to return. Call this "
        "first when asked for test cases, then write them yourself from what it "
        "returns. No separate API key is used."
    ),
    annotations=READ_ONLY,
)
def jira_test_case_brief(key: str) -> str:
    """`key` is the issue to write test cases for."""

    def run() -> str:
        jira = _client()
        try:
            context = jira.get_context(key.strip().upper())
        finally:
            jira.close()

        warning = (
            ""
            if context["description"]
            else "\n**Warning:** this issue has no description. Say so rather than "
            "inventing requirements.\n"
        )
        return (
            "# Rules for writing these test cases\n\n"
            f"{TEST_CASE_RULES}\n"
            f"{warning}\n"
            "# The ticket\n\n"
            f"{build_prompt(context)}\n\n"
            "# Return shape\n\n"
            "Produce JSON matching this schema. Keep it exactly — "
            "`jira_publish_test_cases` reads this shape.\n\n"
            "```json\n"
            f"{json.dumps(TEST_CASE_SCHEMA, indent=2)}\n"
            "```"
        )

    return _guard(run)


@server.tool(
    description=(
        "Render test cases as CSV, one row per step — the shape Xray, Zephyr and "
        "TestRail import. Pass the JSON produced from jira_test_case_brief."
    ),
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False),
)
def test_cases_to_csv(test_cases_json: str) -> str:
    """`test_cases_json` is the object with a "test_cases" array."""
    try:
        data = json.loads(test_cases_json)
    except ValueError as exc:
        return f"That is not valid JSON: {exc}"
    if not isinstance(data, dict) or "test_cases" not in data:
        return 'Expected an object with a "test_cases" array.'
    return render_csv(data)


# ---------------------------------------------------------------- writing


def _parse_cases(test_cases_json: str) -> tuple[dict[str, Any] | None, str]:
    try:
        data = json.loads(test_cases_json)
    except ValueError as exc:
        return None, f"That is not valid JSON: {exc}"
    if not isinstance(data, dict) or not isinstance(data.get("test_cases"), list):
        return None, 'Expected an object with a "test_cases" array.'
    if not data["test_cases"]:
        return None, "There are no test cases to publish."
    return data, ""


@server.tool(
    description=(
        "Publish test cases to a Jira issue, as one comment or as one sub-task "
        "each. Previews by default: nothing is written unless dry_run is false, "
        "and the server must have been started with writing enabled."
    ),
    annotations=WRITES,
)
def jira_publish_test_cases(
    key: str,
    test_cases_json: str,
    as_subtasks: bool = False,
    dry_run: bool = True,
) -> str:
    """Post test cases to `key`. A comment is reversible; sub-tasks are not."""
    data, problem = _parse_cases(test_cases_json)
    if problem:
        return problem

    issue_key = key.strip().upper()
    cases = data["test_cases"]
    body = render_plain(data, issue_key)

    if dry_run:
        where = (
            f"{len(cases)} sub-tasks under {issue_key}"
            if as_subtasks
            else f"one comment on {issue_key}"
        )
        undo = (
            "None from this tool — deleting issues needs a Jira permission most "
            "accounts lack."
            if as_subtasks
            else "Delete the comment in Jira."
        )
        enabled = "yes" if _writes_allowed() else f"NO — {WRITES_ENABLED} is not set"
        return (
            f"PREVIEW ONLY — nothing was written.\n\n"
            f"Would create: {where}\n"
            f"Undo: {undo}\n"
            f"Writing enabled on this server: {enabled}\n\n"
            "--- content ---\n\n"
            f"{body}\n\n"
            "--- end ---\n\n"
            "To publish, call again with dry_run=false. The human will be asked "
            "to approve that call."
        )

    if not _writes_allowed():
        return _refusal()

    def run() -> str:
        config = _config()
        project = issue_key.split("-")[0]
        writer = JiraWriter(
            config.jira_url, config.jira_email, config.jira_token, deployment=config.deployment
        )
        created: list[str] = []
        try:
            if not as_subtasks:
                comment = writer.add_comment(issue_key, body, confirmed=True)
                return (
                    f"Posted comment {comment.get('id', '')} on {issue_key}: "
                    f"{config.jira_url}/browse/{issue_key}"
                )
            for case in cases:
                summary = f"{case.get('id', '')} {case.get('title', '')}".strip()
                issue = writer.create_subtask(
                    parent_key=issue_key,
                    project_key=project,
                    summary=summary,
                    description=render_case(case),
                    confirmed=True,
                )
                created.append(issue.get("key", "?"))
            return f"Created {len(created)} sub-tasks under {issue_key}: {', '.join(created)}"
        except JiraError as exc:
            if created:
                return (
                    f"Jira error after creating {len(created)} sub-tasks "
                    f"({', '.join(created)}), which remain in Jira: {exc}"
                )
            raise
        finally:
            writer.close()

    return _guard(run)


def main() -> None:
    """Entry point: `python -m storygen.mcp_server`."""
    server.run("stdio")


if __name__ == "__main__":
    main()
