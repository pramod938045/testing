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

from . import jira_query
from .agent import ChatResult, ToolCallRecord
from .jira_client import JiraClient, JiraError

# Matches ABC-123 / UPAMCORE-30728 anywhere in a sentence.
ISSUE_KEY = re.compile(r"\b([A-Za-z][A-Za-z0-9_]{1,30}-\d+)\b")
MAX_KEYS_PER_MESSAGE = 3
DESCRIPTION_LIMIT = 2000
MAX_RESULTS = 25

# --- what the message is asking for ----------------------------------------
# These are tested *before* the single-issue follow-up table, because a
# question about many issues ("what is blocked right now?") otherwise matches
# a single-issue rule ("block") and answers about the wrong ticket.

WANTS_CHANGE_REQUEST = re.compile(
    r"\bcr\b|\bcrs\b|change request|change ticket|change record", re.I
)
WANTS_EPIC_CHILDREN = re.compile(
    # "what stories are under this epic" — allow words between noun and
    # preposition, but require an epic-ish object after it, so that
    # "issues in the last 7 days" stays a search rather than an epic lookup.
    r"\b(?:stories|issues|tickets|children|child issues|tasks)\b(?:\s+\w+){0,3}?\s+"
    r"(?:under|in|inside|of|below|for|belonging to|attached to)\s+"
    r"(?:this|that|the\s+epic|epic|[A-Za-z][A-Za-z0-9_]{1,20}-\d+)\b|"
    r"\bunder (?:this|that|the) epic\b|"
    r"\bepic'?s? (?:stories|children|issues|tickets)\b|"
    r"what(?:'s| is)? in (?:this|that|the) epic\b",
    re.I,
)
WANTS_SPRINT = re.compile(r"\bsprint\b|\bstandup\b|\bstand-up\b", re.I)
WANTS_PROJECTS = re.compile(
    r"(?:what|which|list|show|see)\b[^?]*\bprojects?\b|\bprojects? can i\b", re.I
)
# Issue types that count as a Change Request, matched against the type name
# Jira returns — never inferred from the key prefix or the link alone.
CHANGE_REQUEST_TYPE = re.compile(r"change\s*request|^change$|^cr$|change record", re.I)

HELP = (
    "I'm reading your real Jira live, without the AI. I can:\n\n"
    "- **Fetch an issue** — type its issue key, e.g. `UPAMCORE-30728`\n"
    "- **Follow up on it** — *what's the status?*, *who is it assigned to?*, "
    "*show the description*, *show the comments*\n"
    "- **Find its Change Request** — *provide the CR ticket for this story*\n"
    "- **List an epic's children** — *what stories are under this epic?*\n"
    "- **Search** — *what's assigned to me and not done?*, "
    "*show bugs updated in the last 7 days*, *what is blocked right now?*\n"
    "- **Summarise the current sprint**\n"
    "- **List projects** — *what projects can I see?*\n"
    "- **Run JQL directly** — start the message with `jql:`\n\n"
    "Everything comes from your Jira, and nothing is ever written to it."
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


def render_results(result: dict[str, Any], label: str) -> str:
    """A search result as a table. Every row comes from Jira."""
    issues = result.get("issues") or []
    jql = result.get("jql", "")

    if not issues:
        return (
            f"No issues {label}.\n\n"
            f"Jira ran this and matched nothing:\n\n```\n{jql}\n```"
        )

    total = result.get("total")
    shown = f"{len(issues)} of {total}" if total and total > len(issues) else str(len(issues))
    lines = [
        f"**{shown} issue(s)** {label}:",
        "",
        "| Key | Summary | Status | Assignee |",
        "| --- | --- | --- | --- |",
    ]
    for issue in issues:
        summary = (issue.get("summary") or "").replace("|", "\\|")
        lines.append(
            f"| **{issue.get('key')}** | {summary} | {issue.get('status') or '—'} "
            f"| {issue.get('assignee') or 'Unassigned'} |"
        )
    lines += ["", f"JQL: `{jql}`"]
    return "\n".join(lines)


def render_sprint(sprint: dict[str, Any], report: dict[str, Any]) -> str:
    """Summarise a real sprint from its real issues."""
    lines = [f"**{sprint['name']}** — {report['total']} issue(s)"]
    if sprint.get("goal"):
        lines += ["", f"_Goal: {sprint['goal']}_"]
    dates = " → ".join(part[:10] for part in (sprint.get("start"), sprint.get("end")) if part)
    if dates:
        lines.append(f"_{dates}_")

    if report.get("by_status"):
        lines += ["", "**By status**", ""]
        lines += [
            f"- {status}: {count}"
            for status, count in sorted(report["by_status"].items(), key=lambda item: -item[1])
        ]
    if report.get("by_assignee"):
        lines += ["", "**By assignee**", ""]
        lines += [
            f"- {person}: {count}"
            for person, count in sorted(report["by_assignee"].items(), key=lambda item: -item[1])
        ]

    issues = report.get("issues") or []
    if issues:
        lines += ["", "**Issues**", "", "| Key | Summary | Status | Assignee |", "| --- | --- | --- | --- |"]
        for issue in issues[:MAX_RESULTS]:
            summary = (issue.get("summary") or "").replace("|", "\\|")
            lines.append(
                f"| **{issue.get('key')}** | {summary} | {issue.get('status') or '—'} "
                f"| {issue.get('assignee') or 'Unassigned'} |"
            )
        if len(issues) > MAX_RESULTS:
            lines.append("")
            lines.append(f"_… and {len(issues) - MAX_RESULTS} more._")
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

    def __init__(self, jira: JiraClient, default_project: str = ""):
        self.jira = jira
        self.default_project = default_project
        self.messages: list[dict[str, Any]] = []
        self.last_issue: dict[str, Any] | None = None

    def reset(self) -> None:
        self.messages = []
        self.last_issue = None

    # ------------------------------------------------------------- searching

    async def _search(self, query: jira_query.Query, limit: int = MAX_RESULTS):
        """Run a Query's candidates until Jira accepts one.

        A rejected candidate means the field does not exist on this site —
        `statusCategory`, `"Epic Link"`, a `Blocked` status — not that the
        question was wrong, so the next, coarser candidate is tried. Only when
        every candidate is rejected does the error reach the user, with the JQL
        that produced it.
        """
        last_error: JiraError | None = None
        for jql in query.candidates:
            try:
                result = await self.jira.search(jql, max_results=limit)
            except JiraError as exc:
                # 400 = this site cannot run that JQL. Anything else is real.
                if exc.status_code != 400:
                    raise
                last_error = exc
                continue
            return result, ToolCallRecord(
                name="search",
                args={"jql": jql},
                ok=True,
                summary=f'{{"count": {result["count"]}}}',
            )

        message = last_error.message if last_error else "no query could be built"
        raise JiraError(
            f"Jira rejected the search.\n\n> {message}\n\nTried: `{query.candidates[-1]}`",
            400,
        )

    async def _run(self, query: jira_query.Query) -> tuple[str, list[ToolCallRecord]]:
        """Search and render, reporting Jira's own words on failure."""
        try:
            result, call = await self._search(query)
        except JiraError as exc:
            return exc.message, [
                ToolCallRecord("search", {"jql": query.jql}, False, exc.message[:200])
            ]

        return render_results(result, query.label), [call]

    async def chat(self, user_message: str) -> ChatResult:
        self.messages.append({"role": "user", "content": user_message})
        reply, calls = await self._answer(user_message)
        self.messages.append({"role": "assistant", "content": reply})
        return ChatResult(reply=reply, tool_calls=calls)

    async def _answer(self, message: str) -> tuple[str, list[ToolCallRecord]]:
        keys = find_keys(message)

        # Raw JQL, for anything the translator below cannot express.
        if message.strip().lower().startswith("jql:"):
            jql = message.split(":", 1)[1].strip()
            return await self._run(jira_query.Query([jql], "your JQL"))

        # --- questions about more than one issue, decided first -------------
        if WANTS_CHANGE_REQUEST.search(message):
            return await self._change_request(keys)
        # Sprint before epic: "issues in this sprint" matches both, and only
        # the sprint pattern requires the word "sprint" to be there.
        if WANTS_SPRINT.search(message):
            return await self._sprint(keys)
        if WANTS_EPIC_CHILDREN.search(message):
            return await self._epic_children(keys)
        if WANTS_PROJECTS.search(message):
            return await self._list_projects()

        # A bare key means "show me this issue", not "search for it".
        if keys:
            parts: list[str] = []
            calls: list[ToolCallRecord] = []
            for key in keys:
                rendered, call = await self._fetch(key)
                parts.append(rendered)
                calls.append(call)
            return "\n\n---\n\n".join(parts), calls

        # A question that names its own scope — "assigned to me", "blocked",
        # "bugs in the last 7 days" — is a search, and must be decided before
        # the follow-up table below, whose rules match on bare substrings and
        # would answer it about whichever issue happens to be open.
        query = jira_query.build(message, self.default_project)
        if query is not None and query.specific:
            return await self._run(query)

        # --- a follow-up about the issue already on screen ------------------
        if self.last_issue is not None:
            answer = answer_about(self.last_issue, message)
            if answer is not None:
                return answer, []

        # "what is not done?" with nothing open can only mean a search.
        if query is not None:
            return await self._run(query)

        if self.last_issue is not None:
            return (
                f"I still have **{self.last_issue['key']}** open, but I couldn't read that "
                "question.\n\n" + HELP,
                [],
            )
        return HELP, []

    # --------------------------------------------------------- related issues

    async def _subject(self, keys: list[str]) -> tuple[dict[str, Any] | None, list[ToolCallRecord], str]:
        """The issue a question is about: one named in the message, else the
        last one fetched. Returns (issue, calls, message_if_none)."""
        if keys:
            _, call = await self._fetch(keys[0])
            if not call.ok:
                return None, [call], f"I could not read **{keys[0]}** from Jira."
            return self.last_issue, [call], ""
        if self.last_issue is not None:
            return self.last_issue, [], ""
        return None, [], "Which issue do you mean? Type its key, e.g. `UPAMCORE-30728`."

    async def _types_of(self, keys: list[str]) -> tuple[dict[str, dict[str, Any]], ToolCallRecord | None]:
        """Look up several issues at once, so the *type* comes from Jira.

        Link data on an issue does not carry the linked issue's type, and the
        type is the only trustworthy way to tell a Change Request from any
        other neighbour — the key prefix and the link name are not.
        """
        if not keys:
            return {}, None
        jql = f"key in ({', '.join(keys)})"
        try:
            result = await self.jira.search(jql, max_results=len(keys))
        except JiraError:
            # One unreadable key fails the whole batch; fall back to one by one.
            found: dict[str, dict[str, Any]] = {}
            for key in keys:
                try:
                    found[key] = await self.jira.get_issue(key)
                except JiraError:
                    continue
            return found, ToolCallRecord(
                "get_issue", {"keys": keys}, True, f'{{"resolved": {len(found)}}}'
            )

        by_key = {issue["key"]: issue for issue in result.get("issues") or []}
        return by_key, ToolCallRecord("search", {"jql": jql}, True, f'{{"count": {len(by_key)}}}')

    async def _change_request(self, keys: list[str]) -> tuple[str, list[ToolCallRecord]]:
        """Find the Change Request related to an issue, using real link data.

        Candidates are the issue's parent, its links and its subtasks. Each is
        looked up so its *issue type* decides whether it is a CR — a linked
        issue is not assumed to be one.
        """
        issue, calls, problem = await self._subject(keys)
        if issue is None:
            return problem, calls

        key = issue["key"]
        # (candidate key, how it relates to this issue)
        related: list[tuple[str, str]] = []
        if issue.get("parent"):
            related.append((issue["parent"], "parent of this issue"))
        for link in issue.get("links") or []:
            related.append((link["key"], link["relation"]))
        for sub in issue.get("subtasks") or []:
            related.append((sub["key"], "subtask of this issue"))

        # De-duplicate, keeping the first relationship seen for each key.
        seen: dict[str, str] = {}
        for candidate, relation in related:
            seen.setdefault(candidate, relation)

        if not seen:
            return (
                f"**{key}** has no parent, no linked issues and no subtasks, so there is "
                "no Change Request linked to it in Jira.",
                calls,
            )

        found, call = await self._types_of(list(seen))
        if call:
            calls.append(call)

        change_requests = [
            (candidate, seen[candidate], found[candidate])
            for candidate in seen
            if candidate in found
            and CHANGE_REQUEST_TYPE.search(found[candidate].get("type") or "")
        ]

        if not change_requests:
            lines = [
                f"**No Change Request is linked to {key}.**",
                "",
                f"I checked all {len(seen)} related issue(s) and none has a Change Request "
                "issue type:",
                "",
            ]
            for candidate, relation in seen.items():
                detail = found.get(candidate) or {}
                type_name = detail.get("type") or "unreadable"
                summary = detail.get("summary") or ""
                lines.append(f"- {relation} **{candidate}** — _{type_name}_ — {summary}")
            return "\n".join(lines), calls

        heading = (
            f"**{len(change_requests)} possible Change Requests for {key}** — "
            "they relate to it differently, so check which you mean:"
            if len(change_requests) > 1
            else f"**Change Request for {key}:**"
        )
        lines = [heading, ""]
        for candidate, relation, detail in change_requests:
            lines.append(
                f"- **{candidate}** — {detail.get('summary') or ''}\n"
                f"  - Type: _{detail.get('type')}_ · Status: {detail.get('status') or '—'}"
                f" · Assignee: {detail.get('assignee') or 'Unassigned'}\n"
                f"  - Relationship: {key} **{relation}** {candidate}"
            )
        return "\n".join(lines), calls

    async def _epic_children(self, keys: list[str]) -> tuple[str, list[ToolCallRecord]]:
        """The real child issues of an epic, from Jira's own link fields."""
        key = keys[0] if keys else ""
        note = ""
        if not key and self.last_issue is not None:
            key = self.last_issue["key"]
            # "under this epic" asked while a Story is open means that story's
            # epic, not the story — a story has no children of its own.
            parent = self.last_issue.get("parent")
            if parent and (self.last_issue.get("type") or "").lower() != "epic":
                note = (
                    f"**{key}** is a _{self.last_issue.get('type')}_, so I used its parent "
                    f"**{parent}**.\n\n"
                )
                key = parent
        if not key:
            return ("Which epic? Type its key, e.g. `UPAMCORE-30001`.", [])

        reply, calls = await self._run(jira_query.epic_children(key))
        if reply.startswith("No issues"):
            reply += f"\n\nNothing in Jira lists **{key}** as its epic."
        return note + reply, calls

    # ---------------------------------------------------------------- sprints

    async def _sprint(self, keys: list[str]) -> tuple[str, list[ToolCallRecord]]:
        """The real active sprint for a project, and what is in it.

        Needs the Agile API and a board, so each step that can be missing says
        exactly what was missing rather than reporting an empty sprint.
        """
        project = (
            keys[0].split("-")[0]
            if keys
            else (self.last_issue or {}).get("project_key") or self.default_project
        )
        if not project:
            return (
                "Which project's sprint? Fetch an issue first, or name the project — "
                "e.g. *summarise the current sprint in UPAMCORE*.",
                [],
            )

        calls: list[ToolCallRecord] = []
        try:
            boards = (await self.jira.list_boards(project_key=project)).get("boards") or []
            calls.append(
                ToolCallRecord("list_boards", {"project": project}, True, f'{{"count": {len(boards)}}}')
            )
        except JiraError as exc:
            return (
                f"Could not read boards for **{project}**:\n\n> {exc.message}\n\n"
                "Sprints come from Jira's Agile API — if this Jira has no Agile/Software "
                "licence, or the account cannot see the board, sprint data is unavailable.",
                [ToolCallRecord("list_boards", {"project": project}, False, exc.message[:200])],
            )

        if not boards:
            return (
                f"**{project}** has no board your account can see, so there is no sprint "
                "to summarise. Sprints belong to boards, not to projects.",
                calls,
            )

        for board in boards:
            try:
                sprints = (await self.jira.list_sprints(board["id"], state="active")).get("sprints") or []
            except JiraError:
                continue
            if sprints:
                break
        else:
            sprints = []

        if not sprints:
            names = ", ".join(board["name"] for board in boards[:5])
            return (
                f"No **active** sprint on {project}'s board(s): {names}.\n\n"
                "Either the sprint has not been started, or these are Kanban boards, "
                "which have no sprints.",
                calls,
            )

        sprint = sprints[0]
        try:
            report = await self.jira.sprint_report(sprint["id"])
        except JiraError as exc:
            return f"Could not read sprint **{sprint['name']}**:\n\n> {exc.message}", calls
        calls.append(
            ToolCallRecord(
                "sprint_report", {"sprint_id": sprint["id"]}, True, f'{{"total": {report["total"]}}}'
            )
        )
        return render_sprint(sprint, report), calls

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
