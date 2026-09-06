"""Turn a plain-English question into JQL, without the AI.

Real Jira mode runs with no Anthropic key, so this is deliberately rule-based:
deterministic, inspectable, and honest about what it does not understand. A
question it cannot read returns None rather than a guess, and every query it
does build is shown to the user alongside the results.

Some JQL is instance-specific — `statusCategory`, what "blocked" means, whether
epics use `parent` or `"Epic Link"` — so a Query carries *candidates*: the best
query first, simpler ones after it. The caller runs them in order and keeps the
first Jira accepts, which is how one translator works on both Cloud and Data
Center without knowing the site's configuration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ORDER = " ORDER BY updated DESC"

# Issue-type words -> the type name to filter on. Plural and singular both
# appear in questions ("show bugs", "is this story done").
TYPES = {
    "bug": "Bug",
    "bugs": "Bug",
    "story": "Story",
    "stories": "Story",
    "task": "Task",
    "tasks": "Task",
    "epic": "Epic",
    "epics": "Epic",
    "incident": "Incident",
    "incidents": "Incident",
    "defect": "Bug",
    "defects": "Bug",
}

# "assigned to me", "my open bugs", "what's on my plate"
MINE = re.compile(
    r"assigned to me|\bmy (?:open |unresolved |current )?(?:issues?|tickets?|bugs?|stories|"
    r"tasks?|work|items?)|on my plate|i am working on|i'm working on",
    re.I,
)
# "not done", "still open", "open bugs" — but never a bare "open", which shows
# up in "open the description" and would silently filter a lookup.
UNDONE = re.compile(
    r"not done|not yet done|unresolved|still open|outstanding|not (?:yet )?(?:finished|closed|"
    r"complete|completed|resolved)|\bopen (?:issues?|tickets?|bugs?|stories|tasks?|items?)",
    re.I,
)
DONE = re.compile(r"\b(?:done|closed|completed|resolved|finished|shipped)\b", re.I)
BLOCKED = re.compile(r"\bblocked\b|\bblockers?\b|\bimpediments?\b|\bon hold\b|\bstuck\b", re.I)
# "last 7 days", "past 2 weeks", "this week", "today"
RECENT = re.compile(
    r"(?:last|past|previous)\s+(\d+)\s*(day|week|month)s?|"
    r"(?:in the )?(today|yesterday|this week|this month|last week|last month)",
    re.I,
)
# "in UPAMCORE", "for project DFE"
PROJECT = re.compile(r"\b(?:in|for|from|on)\s+(?:project\s+)?([A-Z][A-Z0-9_]{1,20})\b")
UNASSIGNED = re.compile(r"\bunassigned\b|\bnobody\b|\bno (?:one|owner|assignee)\b", re.I)

NAMED_WINDOWS = {
    "today": ("-1d", "today"),
    "yesterday": ("-2d", "since yesterday"),
    "this week": ("-7d", "in the last week"),
    "last week": ("-7d", "in the last week"),
    "this month": ("-30d", "in the last month"),
    "last month": ("-30d", "in the last month"),
}

# What "blocked" means depends on how the team works, and the wrong guess is a
# JQL 400 rather than a wrong answer. Try each until Jira accepts one.
BLOCKED_CLAUSES = [
    'issueLinkType = "is blocked by"',
    "status = Blocked",
    'status in (Blocked, "On Hold")',
    "labels in (blocked, blocker, impediment)",
    "flagged is not EMPTY",
]

# `statusCategory` is standard but not universal on older Data Center sites.
UNDONE_CLAUSES = ["statusCategory != Done", "resolution = EMPTY"]
DONE_CLAUSES = ["statusCategory = Done", "resolution is not EMPTY"]


@dataclass
class Query:
    """JQL to run, plainest-English description, and simpler fall-backs.

    `candidates` is ordered best-first. The caller tries them in turn and uses
    the first Jira does not reject, so an unsupported field degrades into a
    coarser query instead of an error.
    """

    candidates: list[str]
    label: str
    # True when the question names a scope of its own — an assignee, a type, a
    # project, a time window, "blocked". A query built only from "done" or
    # "not done" is not specific: "is it done?" is a follow-up about the open
    # issue, not a request to search the whole site.
    specific: bool = True

    @property
    def jql(self) -> str:
        return self.candidates[0]


def _window(text: str) -> tuple[str, str] | None:
    """('-7d', 'in the last 7 days') for whatever time phrase is present."""
    match = RECENT.search(text)
    if not match:
        return None
    amount, unit, named = match.group(1), match.group(2), match.group(3)
    if named:
        return NAMED_WINDOWS.get(named.lower())
    days = int(amount) * {"day": 1, "week": 7, "month": 30}[unit.lower()]
    plural = "s" if int(amount) != 1 else ""
    return f"-{days}d", f"in the last {amount} {unit.lower()}{plural}"


def _issue_type(text: str) -> str | None:
    for word in re.findall(r"[a-z-]+", text.lower()):
        if word in TYPES:
            return TYPES[word]
    return None


def build(text: str, default_project: str = "") -> Query | None:
    """JQL for a question, or None when nothing recognisable is being asked.

    Returning None matters as much as returning a query: it is what stops a
    follow-up like "who is it assigned to?" from being run as a search across
    the whole site.
    """
    lowered = text.lower()
    clauses: list[str] = []
    described: list[str] = []

    if MINE.search(text):
        clauses.append("assignee = currentUser()")
        described.append("assigned to you")
    elif UNASSIGNED.search(text):
        clauses.append("assignee is EMPTY")
        described.append("unassigned")

    issue_type = _issue_type(lowered)
    if issue_type:
        clauses.append(f'issuetype = "{issue_type}"')
        described.append(f"{issue_type.lower()}s")

    project = PROJECT.search(text)
    project_key = project.group(1) if project else ""
    if not project_key and default_project:
        project_key = default_project
    if project_key:
        clauses.append(f"project = {project_key}")
        described.append(f"in {project_key}")

    window = _window(lowered)
    if window:
        clauses.append(f"updated >= {window[0]}")
        described.append(f"updated {window[1]}")

    # Everything above names a scope of its own; "done"/"not done" alone does not.
    specific = bool(clauses)

    # One "risky" clause at most — the one whose JQL varies between sites.
    variants: list[str] = []
    if BLOCKED.search(lowered):
        variants = BLOCKED_CLAUSES
        described.append("blocked")
        specific = True
    elif UNDONE.search(lowered):
        variants = UNDONE_CLAUSES
        described.append("not done")
    elif DONE.search(lowered):
        variants = DONE_CLAUSES
        described.append("done")

    if not clauses and not variants:
        return None

    def assemble(extra: str | None) -> str:
        parts = clauses + ([extra] if extra else [])
        return " AND ".join(parts) + ORDER

    candidates = [assemble(variant) for variant in variants] if variants else [assemble(None)]
    return Query(
        candidates=candidates,
        label=" ".join(described) or "matching issues",
        specific=specific,
    )


# --- epic children ----------------------------------------------------------

# Company-managed epics on Data Center use the "Epic Link" custom field;
# team-managed and newer Cloud projects use `parent`. Try both.
def epic_children(key: str) -> Query:
    return Query(
        candidates=[
            f'"Epic Link" = {key}{ORDER}',
            f"parent = {key}{ORDER}",
            f'"Parent Link" = {key}{ORDER}',
        ],
        label=f"under **{key}**",
    )
