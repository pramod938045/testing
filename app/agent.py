"""The chat agent: Claude + the Jira tools, driven by a manual tool-use loop.

A manual loop (rather than the SDK's beta tool runner) is used deliberately —
it lets the server report every tool call back to the browser so the UI can
show what the bot actually did in Jira, and keeps the write-guard in one place.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import anthropic

from .config import Settings
from .jira_client import JiraClient
from .tools import execute_tool, tool_definitions

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a Jira assistant. You help the user query and manage their Jira Cloud site \
through the tools you have been given. You are talking to them in a chat window.

## How to work
- Answer from real Jira data. Never invent issue keys, statuses, assignees, sprint names or counts — \
if you do not have the data, call a tool.
- Prefer one well-built JQL query over many small lookups. `search_issues` handles most questions.
- "me"/"my"/"mine" means the authenticated Jira user: use `assignee = currentUser()` in JQL.
- "this sprint"/"current sprint" is `sprint in openSprints()`. For a sprint digest grouped by status \
and assignee, get the board with `list_boards`, the sprint with `list_sprints`, then `sprint_report`.
- Resolve people to an account id with `find_user` before assigning anything. If several users match, \
ask which one.
- If a project is named in words rather than by key, resolve it with `list_projects` first.
- When a tool returns an error, tell the user plainly what failed and what would fix it. Do not retry \
the identical call.

## Making changes
Creating issues, updating fields, transitioning status, commenting and logging work all change real \
data that other people see.
- Before the first write of a request, restate exactly what you are about to do (project, issue key, \
field values, comment text) and ask the user to confirm. Wait for a clear yes.
- Once the user has confirmed a specific action, carry it out fully without asking again.
- If a request is ambiguous about *which* issue, ask before writing — never guess at an issue key.
- After a successful write, report what changed and include the issue link.

## Style
- Be concise and factual. Lead with the answer.
- Format issue lists as markdown lists: `**KEY** — summary _(Status, Assignee)_`.
- Use a markdown table only when comparing more than about six issues across several fields.
- Include the issue URL when the user will want to click through; do not paste a wall of links.
- Say "no matching issues" plainly when a search comes back empty — that is a valid answer."""


@dataclass
class ToolCallRecord:
    """One tool invocation, surfaced to the UI so the user can audit the bot."""

    name: str
    args: dict[str, Any]
    ok: bool
    summary: str


@dataclass
class ChatResult:
    reply: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)


def _summarise(result: Any, limit: int = 200) -> str:
    if isinstance(result, str):
        text = result
    else:
        try:
            text = json.dumps(result, default=str)
        except (TypeError, ValueError):
            text = str(result)
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _as_tool_content(result: Any) -> str:
    if isinstance(result, str):
        return result
    return json.dumps(result, default=str, ensure_ascii=False)


class JiraChatAgent:
    """Holds one conversation. Create one per chat session."""

    def __init__(self, jira: JiraClient, settings: Settings, jira_user: dict[str, Any] | None = None):
        self.jira = jira
        self.settings = settings
        self.jira_user = jira_user or {}
        self.messages: list[dict[str, Any]] = []
        self.client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key or None,
            max_retries=3,
        )

    # ------------------------------------------------------------------ setup

    def _system_blocks(self) -> list[dict[str, Any]]:
        context_lines = [
            f"Jira site: {self.settings.jira_base_url}",
            f"Today's date: {datetime.now(timezone.utc).date().isoformat()} (UTC)",
        ]
        if self.jira_user.get("display_name"):
            context_lines.append(
                f"Authenticated Jira user: {self.jira_user['display_name']}"
                + (f" <{self.jira_user['email']}>" if self.jira_user.get("email") else "")
            )
        if self.settings.jira_default_project:
            context_lines.append(
                f"Default project when the user does not name one: {self.settings.jira_default_project}"
            )
        if not self.settings.allow_writes:
            context_lines.append(
                "READ-ONLY MODE: write tools are unavailable. If the user asks for a change, say so."
            )
        # Stable prefix first, volatile context after, so the cache breakpoint holds.
        return [
            {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "## Environment\n" + "\n".join(context_lines)},
        ]

    def reset(self) -> None:
        self.messages = []

    def _trim_history(self) -> None:
        """Drop the oldest turns, keeping the history starting at a user turn.

        Cutting mid tool-use/tool-result pair would make the next request invalid.
        """
        limit = self.settings.history_turns * 2
        if len(self.messages) <= limit:
            return
        cut = len(self.messages) - limit
        while cut < len(self.messages) and not self._is_plain_user_turn(self.messages[cut]):
            cut += 1
        if cut < len(self.messages):
            self.messages = self.messages[cut:]

    @staticmethod
    def _is_plain_user_turn(message: dict[str, Any]) -> bool:
        if message.get("role") != "user":
            return False
        content = message.get("content")
        if isinstance(content, str):
            return True
        return not any(
            isinstance(block, dict) and block.get("type") == "tool_result" for block in content or []
        )

    # ------------------------------------------------------------------- loop

    async def chat(self, user_message: str) -> ChatResult:
        self.messages.append({"role": "user", "content": user_message})
        self._trim_history()

        tools = tool_definitions(self.settings.allow_writes)
        records: list[ToolCallRecord] = []
        response = None

        for iteration in range(self.settings.max_tool_iterations):
            response = await self.client.messages.create(
                model=self.settings.model,
                max_tokens=self.settings.max_tokens,
                system=self._system_blocks(),
                messages=self.messages,
                tools=tools,
                thinking={"type": "adaptive"},
                output_config={"effort": self.settings.effort},
            )

            if response.stop_reason == "refusal":
                self.messages.append({"role": "assistant", "content": response.content})
                return ChatResult(
                    reply="I can't help with that request.", tool_calls=records
                )

            self.messages.append({"role": "assistant", "content": response.content})

            tool_uses = [block for block in response.content if block.type == "tool_use"]
            if not tool_uses:
                break

            # Parallel calls must all be executed and returned in ONE user message.
            results = await asyncio.gather(
                *(
                    execute_tool(self.jira, block.name, dict(block.input), self.settings.allow_writes)
                    for block in tool_uses
                )
            )

            tool_results = []
            for block, (result, is_error) in zip(tool_uses, results):
                logger.info("tool %s(%s) -> %s", block.name, block.input, "error" if is_error else "ok")
                records.append(
                    ToolCallRecord(
                        name=block.name,
                        args=dict(block.input),
                        ok=not is_error,
                        summary=_summarise(result),
                    )
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": _as_tool_content(result),
                        **({"is_error": True} if is_error else {}),
                    }
                )

            self.messages.append({"role": "user", "content": tool_results})
        else:
            # Loop exhausted without a final answer.
            return ChatResult(
                reply=(
                    "I ran out of steps while working on that "
                    f"({self.settings.max_tool_iterations} tool rounds). "
                    "Could you narrow the request — a single project, sprint or issue?"
                ),
                tool_calls=records,
            )

        text = "\n".join(
            block.text for block in (response.content if response else []) if block.type == "text"
        ).strip()
        if not text:
            text = "I didn't get a usable answer back. Please try rephrasing the question."
        return ChatResult(reply=text, tool_calls=records)
