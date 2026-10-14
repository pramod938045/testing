"""The QA agent: Claude with Jira (read-only) and TestRail tools.

It reuses the tool-use loop of `JiraChatAgent` and adds the TestRail tools.
The one write — adding test cases — never runs on the model's say-so alone:
`create_test_cases` calls `confirm` with the exact cases first, and only an
explicit True from the person creates anything. Without a `confirm` callback
every create is declined.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.agent import JiraChatAgent
from app.config import Settings
from app.jira_client import JiraClient
from app.tools import HANDLERS as JIRA_HANDLERS
from app.tools import execute_tool, tool_definitions

from .config import TestRailSettings
from .testrail_client import TestRailClient, TestRailError
from .tools import CREATE_TOOL, TESTRAIL_TOOL_SPECS, execute_testrail_tool

# Receives the proposal (section, Jira key, cases) and returns True to create it.
Confirm = Callable[[dict[str, Any]], Awaitable[bool]]

MAX_CASES_PER_BATCH = 20

QA_SYSTEM_PROMPT = """You are a QA assistant connected to Jira and TestRail. You help the user \
understand test coverage and quality for their Jira work, and draft TestRail test cases for it.

## What you can do
- Jira: read only. You cannot create, edit, transition, comment on or assign anything in Jira.
- TestRail: read projects, suites, sections, cases, runs and test results — and add NEW test \
cases with `create_test_cases`. You cannot edit or delete cases, or add runs or results.

## How Jira and TestRail connect
A TestRail case is linked to a Jira issue through its References field (e.g. "ABC-123"). \
`testrail_find_cases` with `jira_key` returns the cases covering an issue. To answer "did the \
tests for ABC-123 pass?", find the cases, find the relevant run with `testrail_list_runs`, then \
read its statuses with `testrail_get_run_tests` and match on case_id.

## Creating test cases
1. Read the Jira issue with `get_issue` and base every case on its description and acceptance \
criteria. Do not invent requirements the issue does not state; say what is missing instead.
2. Check existing coverage with `testrail_find_cases` so you do not create duplicates.
3. Pick the section with `testrail_list_sections`. If it is unclear which section, ask the user.
4. Call `create_test_cases` once with the whole batch. The user sees every case and approves or \
declines; nothing is created without their approval. If they decline, accept it and offer to revise.
5. Report what was created with the TestRail links from the tool result. Never claim a case was \
created unless the tool result lists it.

## How to work
- Answer from real data. Never invent issue keys, case ids, run names, statuses or counts.
- When a tool returns an error, tell the user plainly what failed and what would fix it. Do not \
retry the identical call.
- Be concise. Lead with the answer. List cases as `**C123** — title` with the link when useful."""


class QAAgent(JiraChatAgent):
    """One conversation across Jira and TestRail. Create one per session."""

    def __init__(
        self,
        jira: JiraClient,
        testrail: TestRailClient,
        settings: Settings,
        testrail_settings: TestRailSettings,
        confirm: Confirm | None = None,
        jira_user: dict[str, Any] | None = None,
    ):
        super().__init__(jira=jira, settings=settings, jira_user=jira_user)
        self.testrail = testrail
        self.testrail_settings = testrail_settings
        self.confirm = confirm
        # Parallel tool calls must not put two approval prompts in front of a person at once.
        self._confirm_lock = asyncio.Lock()

    def _system_blocks(self) -> list[dict[str, Any]]:
        context = [
            f"Jira site: {self.settings.jira_base_url}",
            f"TestRail site: {self.testrail_settings.base_url}",
            f"Today's date: {datetime.now(timezone.utc).date().isoformat()} (UTC)",
        ]
        if self.jira_user.get("display_name"):
            context.append(f"Authenticated Jira user: {self.jira_user['display_name']}")
        if self.settings.jira_default_project:
            context.append(f"Default Jira project: {self.settings.jira_default_project}")
        if self.testrail_settings.default_project_id:
            context.append(f"Default TestRail project id: {self.testrail_settings.default_project_id}")
        return [
            {"type": "text", "text": QA_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "## Environment\n" + "\n".join(context)},
        ]

    def _tools(self) -> list[dict[str, Any]]:
        return tool_definitions() + [{**spec, "strict": True} for spec in TESTRAIL_TOOL_SPECS]

    async def _execute_tool(self, name: str, args: dict[str, Any]) -> tuple[Any, bool]:
        if name == CREATE_TOOL:
            return await self._create_cases(args)
        if name in JIRA_HANDLERS:
            return await execute_tool(self.jira, name, args)
        return await execute_testrail_tool(self.testrail, name, args)

    async def _create_cases(self, args: dict[str, Any]) -> tuple[Any, bool]:
        try:
            section_id = int(args["section_id"])
            jira_key = str(args["jira_key"]).strip().upper()
            cases = list(args["cases"])
        except (KeyError, ValueError, TypeError) as exc:
            return f"Invalid arguments for '{CREATE_TOOL}': {exc}", True
        if not cases:
            return "No cases were given, so nothing was created.", True
        if len(cases) > MAX_CASES_PER_BATCH:
            return f"At most {MAX_CASES_PER_BATCH} cases per batch; split the request.", True

        try:
            section = await self.testrail.get_section(section_id)
        except TestRailError as exc:
            return f"TestRail error: {exc.message}", True

        proposal = {"section": section, "jira_key": jira_key, "cases": cases}
        async with self._confirm_lock:
            approved = bool(self.confirm and await self.confirm(proposal))
        if not approved:
            return {"created": [], "message": "The user declined. Nothing was created in TestRail."}, False

        created: list[dict[str, Any]] = []
        for case in cases:
            try:
                created.append(await self.testrail.add_case(section_id, case, jira_key))
            except TestRailError as exc:
                return {
                    "created": created,
                    "failed_at": case.get("title"),
                    "error": exc.message,
                    "message": f"Created {len(created)} of {len(cases)} cases, then TestRail refused one.",
                }, True
        return {"created": created, "section": section, "jira_key": jira_key}, False
