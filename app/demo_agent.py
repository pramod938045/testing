"""A stand-in agent for demo mode: canned answers, no Jira and no AI call.

Used to show the chat experience when there is no API key or no credit. It
answers about the sample issues in `storygen.demo_data` — never about the real
Jira site — and every reply says so, so a demo conversation cannot be mistaken
for real data.

It exposes the same `chat()` / `reset()` interface as `JiraChatAgent`, so the
endpoint and the page treat it identically.
"""

from __future__ import annotations

from storygen.demo_data import DEMO_ISSUES, DEMO_STORIES

from .agent import ChatResult, ToolCallRecord

DEMO_NOTE = "\n\n_Demo mode — sample data, not your Jira._"

FALLBACK = (
    "I'm running in **demo mode**, so I can only answer about two sample issues:\n\n"
    "- **DEMO-1** — _Self-service account closure_ (Epic, In Progress)\n"
    "- **DEMO-7** — _First time deposit tracking_ (Change Request, New)\n\n"
    "Try asking:\n\n"
    "- What does DEMO-1 say?\n"
    "- What stories are needed for DEMO-7?\n"
    "- What's assigned to me and not done?\n"
    "- Summarise the current sprint"
)


def _issue_reply(key: str) -> tuple[str, list[ToolCallRecord]]:
    issue = DEMO_ISSUES[key]
    body = [
        f"**{issue['key']}** — {issue['summary']}",
        f"_({issue['type']}, {issue['status']}, project {issue['project']}, "
        f"priority {issue['priority']})_",
        "",
        issue["description"],
    ]
    if issue["links"]:
        body.append("")
        body.append("**Linked issues**")
        body += [
            f"- {link['relation']} **{link['key']}** — {link['summary']} _({link['status']})_"
            for link in issue["links"]
        ]
    if issue["subtasks"]:
        body.append("")
        body.append("**Existing subtasks**")
        body += [f"- **{sub['key']}** — {sub['summary']}" for sub in issue["subtasks"]]

    call = ToolCallRecord(
        name="get_issue",
        args={"key": key},
        ok=True,
        summary=f'{{"key": "{key}", "summary": "{issue["summary"]}", '
        f'"status": "{issue["status"]}", "type": "{issue["type"]}"…',
    )
    return "\n".join(body), [call]


def _stories_reply(key: str) -> tuple[str, list[ToolCallRecord]]:
    issue = DEMO_ISSUES[key]
    result = DEMO_STORIES[key]
    lines = [f"Here are the stories I'd suggest for **{key}** — {issue['summary']}:", ""]

    for index, story in enumerate(result["stories"], start=1):
        lines.append(f"**{index}. {story['summary']}**")
        lines.append("")
        lines.append(story["user_story"])
        lines.append("")
        lines += [f"- {item}" for item in story["acceptance_criteria"]]
        lines.append("")

    if result["open_questions"]:
        lines.append("**Open questions the issue doesn't answer**")
        lines += [f"- {item}" for item in result["open_questions"]]

    call = ToolCallRecord(
        name="get_issue", args={"key": key}, ok=True,
        summary=f'{{"key": "{key}", "summary": "{issue["summary"]}"…',
    )
    return "\n".join(lines), [call]


def _my_issues_reply() -> tuple[str, list[ToolCallRecord]]:
    reply = (
        "You have **2 open items**:\n\n"
        "- **DEMO-1** — Self-service account closure _(Epic, In Progress)_\n"
        "- **DEMO-7** — First time deposit tracking _(Change Request, New)_\n\n"
        "DEMO-1 is the older of the two and blocks DEMO-14, so it's the one I'd "
        "look at first."
    )
    call = ToolCallRecord(
        name="search_issues",
        args={"jql": "assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC"},
        ok=True,
        summary='{"count": 2, "issues": [{"key": "DEMO-1", "status": "In Progress"…',
    )
    return reply, [call]


def _sprint_reply() -> tuple[str, list[ToolCallRecord]]:
    reply = (
        "**Sprint 24** — 2 issues, both still open.\n\n"
        "| Status | Count |\n| --- | --- |\n| In Progress | 1 |\n| New | 1 |\n\n"
        "Nothing is finished yet, and **DEMO-1** is blocked by "
        "**DEMO-14** _(Marketing preferences service migration, In Review)_ — "
        "that's the risk to the sprint."
    )
    calls = [
        ToolCallRecord("list_boards", {"project_key": "DEMO"}, True,
                       '{"boards": [{"id": 1, "name": "DEMO board"}]}'),
        ToolCallRecord("sprint_report", {"sprint_id": 24}, True,
                       '{"total": 2, "by_status": {"In Progress": 1, "New": 1}…'),
    ]
    return reply, calls


def _blocked_reply() -> tuple[str, list[ToolCallRecord]]:
    reply = (
        "One thing is blocked:\n\n"
        "- **DEMO-1** — Self-service account closure, waiting on **DEMO-14** "
        "_(Marketing preferences service migration, In Review)_.\n\n"
        "DEMO-1 can't enforce the 'no marketing after closure' rule until that "
        "migration lands."
    )
    call = ToolCallRecord(
        "search_issues", {"jql": 'project = DEMO AND issueLinkType = "is blocked by"'},
        True, '{"count": 1, "issues": [{"key": "DEMO-1"…',
    )
    return reply, [call]


def answer(message: str) -> tuple[str, list[ToolCallRecord]]:
    """Route a question to a canned answer. Deliberately simple keyword matching."""
    text = message.lower()
    wants_stories = any(
        word in text for word in ("story", "stories", "split", "break down", "breakdown")
    )

    for key in DEMO_ISSUES:
        if key.lower() in text:
            return _stories_reply(key) if wants_stories else _issue_reply(key)

    if "closure" in text or "close account" in text:
        return _stories_reply("DEMO-1") if wants_stories else _issue_reply("DEMO-1")
    if "deposit" in text or "analytics" in text or "tracking" in text:
        return _stories_reply("DEMO-7") if wants_stories else _issue_reply("DEMO-7")

    if "block" in text:
        return _blocked_reply()
    if "sprint" in text:
        return _sprint_reply()
    if any(word in text for word in ("assigned to me", "my ", "mine", "my plate", "not done")):
        return _my_issues_reply()
    if wants_stories:
        return _stories_reply("DEMO-1")

    return FALLBACK, []


class DemoAgent:
    """Same interface as JiraChatAgent, but answers from sample data."""

    def __init__(self) -> None:
        self.messages: list[dict] = []

    def reset(self) -> None:
        self.messages = []

    async def chat(self, user_message: str) -> ChatResult:
        self.messages.append({"role": "user", "content": user_message})
        reply, calls = answer(user_message)
        self.messages.append({"role": "assistant", "content": reply})
        return ChatResult(reply=reply + DEMO_NOTE, tool_calls=calls)
