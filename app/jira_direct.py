"""Real Jira mode: answer from live Jira without the AI.

The AI agent needs an Anthropic key and credit; demo mode needs neither but
answers from samples. This sits between them — it reads the *real* Jira through
the existing `JiraClient` and answers deterministically, so a real issue key
returns real data with no AI involved.

It recognises an issue key anywhere in the message, fetches that issue, and can
then answer follow-up questions about it from what was retrieved. Nothing is
hardcoded: every field shown comes from the Jira response. It is read-only, like
the rest of the app.
"""

from __future__ import annotations

import re
from typing import Any

from .agent import ChatResult, ToolCallRecord
from .jira_client import JiraClient, JiraError

# Matches ABC-123 / UPAMCORE-30728 anywhere in a sentence.
ISSUE_KEY = re.compile(r"\b([A-Za-z][A-Za-z0-9_]{1,30}-\d+)\b")
MAX_KEYS_PER_MESSAGE = 3
DESCRIPTION_LIMIT = 2000

HELP = (
    "I'm reading your real Jira, but without the AI I can only do a few things:\n\n"
    "- **Type an issue key** — e.g. `DFE-9067` — and I'll fetch it from Jira\n"
    "- Ask **what projects can I see?** to find the right key prefix\n"
    "- Then ask follow-ups about it: *what's the status?*, *who is it assigned to?*, "
    "*show the description*, *any linked issues?*, *show the comments*\n\n"
    "For free-form questions across many issues, the AI mode is needed — that "
    "requires an Anthropic key with credit."
)


def find_keys(message: str) -> list[str]:
    """Issue keys in the order written, de-duplicated, upper-cased."""
    seen: list[str] = []
    for match in ISSUE_KEY.findall(message):
        key = match.upper()
        if key not in seen:
            seen.append(key)
    return seen[:MAX_KEYS_PER_MESSAGE]


def _truncate(text: str, limit: int = DESCRIPTION_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"\n\n… _(cut — {len(text)} characters in Jira)_"


def render_issue(issue: dict[str, Any]) -> str:
    """Format a real Jira issue for the chat. Every value comes from Jira."""
    facts = [
        ("Type", issue.get("type")),
        ("Status", issue.get("status")),
        ("Priority", issue.get("priority")),
        ("Assignee", issue.get("assignee")),
        ("Reporter", issue.get("reporter")),
        ("Project", issue.get("project")),
        ("Labels", ", ".join(issue.get("labels") or [])),
        ("Components", ", ".join(issue.get("components") or [])),
        ("Fix versions", ", ".join(issue.get("fix_versions") or [])),
        ("Due", issue.get("due_date")),
        ("Parent", issue.get("parent")),
    ]
    lines = [
        f"**{issue['key']}** — {issue.get('summary') or '(no summary)'}",
        "",
        *[f"- **{label}:** {value}" for label, value in facts if value],
    ]

    description = (issue.get("description") or "").strip()
    lines += ["", "**Description**", "", _truncate(description) if description else "_(empty)_"]

    if issue.get("links"):
        lines += ["", f"**Linked issues ({len(issue['links'])})**"]
        lines += [
            f"- {link['relation']} **{link['key']}** — {link['summary']} _({link['status']})_"
            for link in issue["links"]
        ]

    if issue.get("subtasks"):
        lines += ["", f"**Subtasks ({len(issue['subtasks'])})**"]
        lines += [
            f"- **{sub['key']}** — {sub['summary']} _({sub.get('status', '')})_".replace(" _()_", "")
            for sub in issue["subtasks"]
        ]

    if issue.get("comments"):
        lines += ["", f"**Comments:** {len(issue['comments'])} — ask to see them"]

    lines += ["", issue.get("url", "")]
    return "\n".join(lines)


def answer_about(issue: dict[str, Any], question: str) -> str | None:
    """Answer a follow-up from the issue already retrieved, or None if unsure."""
    text = question.lower()
    key = issue["key"]

    def field(label: str, value: Any, empty: str) -> str:
        return f"**{key}** — {label}: {value}" if value else f"**{key}** — {empty}"

    if "status" in text or "what state" in text:
        return field("status", issue.get("status"), "no status returned by Jira")
    if "assign" in text or "who owns" in text or "owner" in text or "who is working" in text:
        return field("assignee", issue.get("assignee"), "unassigned")
    if "report" in text or "who raised" in text or "who created" in text:
        return field("reporter", issue.get("reporter"), "no reporter returned by Jira")
    if "priority" in text:
        return field("priority", issue.get("priority"), "no priority set")
    if "label" in text:
        return field("labels", ", ".join(issue.get("labels") or []), "no labels")
    if "due" in text:
        return field("due date", issue.get("due_date"), "no due date set")
    if "updated" in text or "changed" in text:
        return field("last updated", issue.get("updated"), "no update time returned")
    if "created" in text or "raised when" in text:
        return field("created", issue.get("created"), "no created time returned")

    if "description" in text or "what does it say" in text or "detail" in text:
        description = (issue.get("description") or "").strip()
        if not description:
            return f"**{key}** has no description in Jira."
        return f"**{key}** — description:\n\n{_truncate(description)}"

    if "comment" in text:
        comments = issue.get("comments") or []
        if not comments:
            return f"**{key}** has no comments."
        lines = [f"**{key}** — {len(comments)} comment(s):", ""]
        for comment in comments[:5]:
            lines.append(f"**{comment['author']}** · {comment['created'][:10]}")
            lines.append(_truncate(comment["body"], 600))
            lines.append("")
        return "\n".join(lines)

    if "link" in text or "block" in text or "related" in text:
        links = issue.get("links") or []
        if not links:
            return f"**{key}** has no linked issues."
        return f"**{key}** — linked issues:\n\n" + "\n".join(
            f"- {link['relation']} **{link['key']}** — {link['summary']} _({link['status']})_"
            for link in links
        )

    if "subtask" in text or "sub-task" in text or "child" in text:
        subtasks = issue.get("subtasks") or []
        if not subtasks:
            return f"**{key}** has no subtasks."
        return f"**{key}** — subtasks:\n\n" + "\n".join(
            f"- **{sub['key']}** — {sub['summary']}" for sub in subtasks
        )

    if "summar" in text or "about" in text or "tell me" in text or "show" in text:
        return render_issue(issue)

    return None


class JiraDirectAgent:
    """Answers from live Jira, with no AI. Same interface as JiraChatAgent."""

    def __init__(self, jira: JiraClient):
        self.jira = jira
        self.messages: list[dict[str, Any]] = []
        self.last_issue: dict[str, Any] | None = None

    def reset(self) -> None:
        self.messages = []
        self.last_issue = None

    async def chat(self, user_message: str) -> ChatResult:
        self.messages.append({"role": "user", "content": user_message})
        reply, calls = await self._answer(user_message)
        self.messages.append({"role": "assistant", "content": reply})
        return ChatResult(reply=reply, tool_calls=calls)

    async def _answer(self, message: str) -> tuple[str, list[ToolCallRecord]]:
        text = message.lower()
        if "project" in text and any(w in text for w in ("what", "which", "list", "show", "see")):
            return await self._list_projects()

        keys = find_keys(message)

        if keys:
            parts: list[str] = []
            calls: list[ToolCallRecord] = []
            for key in keys:
                text, call = await self._fetch(key)
                parts.append(text)
                calls.append(call)
            return "\n\n---\n\n".join(parts), calls

        if self.last_issue is not None:
            answer = answer_about(self.last_issue, message)
            if answer is not None:
                return answer, []
            return (
                f"I still have **{self.last_issue['key']}** open. Ask about its status, "
                "assignee, reporter, priority, description, comments, linked issues or "
                "subtasks — or type another issue key.",
                [],
            )

        return HELP, []

    async def _list_projects(self) -> tuple[str, list[ToolCallRecord]]:
        """Which projects this account can see — the fastest way to find a key prefix."""
        try:
            projects = (await self.jira.list_projects(limit=50)).get("projects") or []
        except JiraError as exc:
            return (
                f"Could not list projects:\n\n> {exc.message}",
                [ToolCallRecord("list_projects", {}, False, exc.message[:200])],
            )

        call = ToolCallRecord("list_projects", {}, True, f'{{"count": {len(projects)}}}')
        if not projects:
            return (f"Your account can see no projects on {self.jira.base_url}.", [call])

        lines = [f"**{len(projects)} project(s)** on {self.jira.base_url}:", ""]
        lines += [
            f"- **{project['key']}** — {project['name']}"
            for project in sorted(projects, key=lambda p: p["key"])
        ]
        lines.append("")
        lines.append("Type an issue key from one of these, e.g. `DFE-9067`.")
        return "\n".join(lines), [call]

    async def _why_not_found(self, key: str) -> str:
        """Say *why* a key is missing: wrong project, or wrong issue number.

        A bare "not found" leaves the user guessing between a typo, a
        permissions problem, and the key belonging to a different Jira site.
        Checking whether the project itself exists separates those cases.
        """
        prefix = key.split("-")[0]
        site = self.jira.base_url

        try:
            matches = (await self.jira.list_projects(query=prefix)).get("projects") or []
        except JiraError:
            return (
                "Either it does not exist, or your account cannot see it. "
                "Check the key and the project's permissions."
            )

        if any(project["key"].upper() == prefix for project in matches):
            return (
                f"Project **{prefix}** does exist on {site}, so the issue number is probably "
                "wrong, the issue was deleted or moved, or your account lacks permission "
                "for that project."
            )

        try:
            visible = (await self.jira.list_projects(limit=50)).get("projects") or []
        except JiraError:
            visible = []

        lines = [
            f"There is no project **{prefix}** on {site} — so this key belongs to a "
            "different Jira site, or the project name is different here."
        ]
        if visible:
            names = ", ".join(sorted(project["key"] for project in visible)[:25])
            lines.append("")
            lines.append(f"Projects your account can see here ({len(visible)}): {names}")
        return "\n".join(lines)

    async def _fetch(self, key: str) -> tuple[str, ToolCallRecord]:
        """Fetch one real issue, reporting Jira's own error if it fails."""
        try:
            issue = await self.jira.get_issue_full(key)
        except JiraError as exc:
            if exc.status_code == 404:
                reply = f"**{key}** was not found in Jira.\n\n" + await self._why_not_found(key)
            elif exc.status_code in (401, 403):
                # Requirement: show the real authentication error, not a generic one.
                reply = f"Jira refused the request for **{key}**:\n\n> {exc.message}"
            else:
                reply = f"Could not read **{key}** from Jira:\n\n> {exc.message}"
            return reply, ToolCallRecord(
                name="get_issue", args={"key": key}, ok=False, summary=exc.message[:200]
            )

        self.last_issue = issue
        call = ToolCallRecord(
            name="get_issue",
            args={"key": key},
            ok=True,
            summary=(
                f'{{"key": "{issue["key"]}", "status": "{issue.get("status")}", '
                f'"assignee": "{issue.get("assignee")}", '
                f'"type": "{issue.get("type")}"}}'
            ),
        )
        return render_issue(issue), call
