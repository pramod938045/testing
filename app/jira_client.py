"""Async Jira Cloud REST client (API v3 + Agile API v1.0).

READ-ONLY BY DESIGN. `_request` refuses anything that could change Jira: only
GET, and POST to the search endpoints (search reads but uses POST), are
allowed. There are no create/update/transition/comment/worklog methods, and a
future edit that tried to add one would raise instead of writing.

Responses are trimmed to the fields that matter so a 50-issue search doesn't
blow up the context window.
"""

from __future__ import annotations

import base64
from typing import Any, Iterable

import httpx

from .adf import render

DEFAULT_FIELDS = [
    "summary",
    "status",
    "assignee",
    "reporter",
    "priority",
    "issuetype",
    "created",
    "updated",
    "duedate",
    "labels",
    "parent",
    "project",
]

DETAIL_FIELDS = DEFAULT_FIELDS + ["description", "subtasks", "issuelinks", "timetracking", "resolution"]

# The only non-GET calls allowed: searching reads data but uses POST.
SEARCH_PATHS = ("/rest/api/3/search/jql", "/rest/api/3/search")


class JiraError(RuntimeError):
    """A Jira API call failed. `message` is safe to show to the user/model."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def escape_jql(value: str) -> str:
    r"""Escape a string for use inside a quoted JQL literal.

    Backslashes first, then double quotes: ``he said "hi"`` -> ``he said \"hi\"``.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


class JiraClient:
    def __init__(
        self,
        base_url: str,
        email: str,
        api_token: str,
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.email = email
        token = base64.b64encode(f"{email}:{api_token}".encode()).decode()
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            headers={
                "Authorization": f"Basic {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ HTTP

    @staticmethod
    def _refuse_writes(method: str, path: str) -> None:
        """Block anything that could modify Jira, before a request is sent."""
        method = method.upper()
        if method == "GET":
            return
        if method == "POST" and path in SEARCH_PATHS:
            return
        raise JiraError(
            f"Blocked: {method} {path} would modify Jira. This assistant is read-only "
            "and never creates, edits or deletes anything."
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        self._refuse_writes(method, path)
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.RequestError as exc:  # DNS failure, timeout, TLS, ...
            raise JiraError(f"Could not reach Jira at {self.base_url}: {exc}") from exc

        if response.status_code == 204 or not response.content:
            return None
        if response.is_success:
            try:
                return response.json()
            except ValueError:
                return response.text

        raise JiraError(self._error_message(response), response.status_code)

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        detail = ""
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            parts: list[str] = list(payload.get("errorMessages") or [])
            errors = payload.get("errors")
            if isinstance(errors, dict):
                parts += [f"{key}: {value}" for key, value in errors.items()]
            if not parts and payload.get("message"):
                parts.append(str(payload["message"]))
            detail = "; ".join(parts)
        if not detail:
            detail = (response.text or "").strip()[:300]

        hints = {
            401: "Check JIRA_EMAIL and JIRA_API_TOKEN.",
            403: "The Jira account lacks permission for this operation.",
            404: "Not found — check the issue key, project or id.",
        }
        hint = hints.get(response.status_code, "")
        return f"Jira returned {response.status_code}. {detail} {hint}".strip()

    # ---------------------------------------------------------------- shaping

    def issue_url(self, key: str) -> str:
        return f"{self.base_url}/browse/{key}"

    def simplify_issue(self, issue: dict[str, Any], include_description: bool = False) -> dict[str, Any]:
        fields = issue.get("fields") or {}

        def name_of(value: Any, attr: str = "name") -> str | None:
            return value.get(attr) if isinstance(value, dict) else None

        data: dict[str, Any] = {
            "key": issue.get("key"),
            "summary": fields.get("summary"),
            "status": name_of(fields.get("status")),
            "status_category": (fields.get("status") or {}).get("statusCategory", {}).get("name"),
            "type": name_of(fields.get("issuetype")),
            "priority": name_of(fields.get("priority")),
            "assignee": name_of(fields.get("assignee"), "displayName"),
            "reporter": name_of(fields.get("reporter"), "displayName"),
            "labels": fields.get("labels") or [],
            "due_date": fields.get("duedate"),
            "created": fields.get("created"),
            "updated": fields.get("updated"),
            "project": name_of(fields.get("project"), "key"),
            "parent": (fields.get("parent") or {}).get("key"),
            "url": self.issue_url(issue.get("key", "")),
        }
        if include_description:
            data["description"] = render(fields.get("description")) or None
            data["subtasks"] = [
                {"key": sub.get("key"), "summary": (sub.get("fields") or {}).get("summary")}
                for sub in fields.get("subtasks") or []
            ]
            timetracking = fields.get("timetracking") or {}
            if timetracking:
                data["time_estimate"] = timetracking.get("remainingEstimate")
                data["time_spent"] = timetracking.get("timeSpent")
        return {key: value for key, value in data.items() if value not in (None, [], {})}

    # ------------------------------------------------------------------ read

    async def myself(self) -> dict[str, Any]:
        me = await self._request("GET", "/rest/api/3/myself")
        return {
            "account_id": me.get("accountId"),
            "display_name": me.get("displayName"),
            "email": me.get("emailAddress"),
            "timezone": me.get("timeZone"),
        }

    async def search(
        self,
        jql: str,
        max_results: int = 25,
        fields: Iterable[str] | None = None,
        next_page_token: str | None = None,
    ) -> dict[str, Any]:
        """Search issues via the current `/search/jql` endpoint.

        Older Jira Cloud sites (and Jira DC) still expose the legacy
        `/rest/api/3/search`; fall back to it when the new route is absent.
        """
        body: dict[str, Any] = {
            "jql": jql,
            "maxResults": max_results,
            "fields": list(fields or DEFAULT_FIELDS),
        }
        if next_page_token:
            body["nextPageToken"] = next_page_token
        try:
            payload = await self._request("POST", "/rest/api/3/search/jql", json=body)
        except JiraError as exc:
            if exc.status_code not in (404, 410):
                raise
            payload = await self._request("POST", "/rest/api/3/search", json=body)

        issues = [self.simplify_issue(issue) for issue in payload.get("issues", [])]
        result: dict[str, Any] = {"jql": jql, "count": len(issues), "issues": issues}
        if payload.get("nextPageToken"):
            result["next_page_token"] = payload["nextPageToken"]
        if payload.get("total") is not None:
            result["total"] = payload["total"]
        return result

    async def get_issue(self, key: str) -> dict[str, Any]:
        payload = await self._request(
            "GET", f"/rest/api/3/issue/{key}", params={"fields": ",".join(DETAIL_FIELDS)}
        )
        return self.simplify_issue(payload, include_description=True)

    async def get_issue_full(self, key: str) -> dict[str, Any]:
        """Everything about one issue, in a single request.

        Powers the ticket-lookup page: description, people, dates, links,
        subtasks and comments. Comments come back inside the same response,
        so no second call is needed.
        """
        fields = DETAIL_FIELDS + ["comment", "fixVersions", "components"]
        payload = await self._request(
            "GET", f"/rest/api/3/issue/{key}", params={"fields": ",".join(fields)}
        )
        f = payload.get("fields") or {}

        def name(value: Any, attr: str = "name") -> str:
            return (value or {}).get(attr, "") if isinstance(value, dict) else ""

        links = []
        for link in f.get("issuelinks") or []:
            other = link.get("outwardIssue") or link.get("inwardIssue")
            if not other:
                continue
            relation = (link.get("type") or {}).get(
                "outward" if link.get("outwardIssue") else "inward", "relates to"
            )
            other_fields = other.get("fields") or {}
            links.append(
                {
                    "relation": relation,
                    "key": other.get("key"),
                    "summary": other_fields.get("summary", ""),
                    "status": name(other_fields.get("status")),
                    "url": self.issue_url(other.get("key", "")),
                }
            )

        comments = [
            {
                "author": (c.get("author") or {}).get("displayName", ""),
                "created": c.get("created", ""),
                "body": render(c.get("body"), limit=4000),
            }
            for c in ((f.get("comment") or {}).get("comments") or [])
        ]

        return {
            "key": payload.get("key"),
            "url": self.issue_url(payload.get("key", "")),
            "summary": f.get("summary", ""),
            "type": name(f.get("issuetype")),
            "status": name(f.get("status")),
            "status_category": (f.get("status") or {}).get("statusCategory", {}).get("name", ""),
            "priority": name(f.get("priority")),
            "resolution": name(f.get("resolution")),
            "project": name(f.get("project")),
            "project_key": name(f.get("project"), "key"),
            "assignee": name(f.get("assignee"), "displayName") or "Unassigned",
            "reporter": name(f.get("reporter"), "displayName"),
            "created": f.get("created", ""),
            "updated": f.get("updated", ""),
            "due_date": f.get("duedate") or "",
            "labels": f.get("labels") or [],
            "components": [name(c) for c in f.get("components") or []],
            "fix_versions": [name(v) for v in f.get("fixVersions") or []],
            "parent": (f.get("parent") or {}).get("key", ""),
            "description": render(f.get("description"), limit=None),
            "subtasks": [
                {
                    "key": s.get("key"),
                    "summary": (s.get("fields") or {}).get("summary", ""),
                    "status": name((s.get("fields") or {}).get("status")),
                    "url": self.issue_url(s.get("key", "")),
                }
                for s in f.get("subtasks") or []
            ],
            "links": links,
            "comments": comments,
        }

    async def get_comments(self, key: str, limit: int = 20) -> dict[str, Any]:
        payload = await self._request(
            "GET",
            f"/rest/api/3/issue/{key}/comment",
            params={"maxResults": limit, "orderBy": "-created"},
        )
        comments = [
            {
                "id": comment.get("id"),
                "author": (comment.get("author") or {}).get("displayName"),
                "created": comment.get("created"),
                "body": render(comment.get("body")),
            }
            for comment in payload.get("comments", [])
        ]
        return {"key": key, "count": len(comments), "comments": comments}

    async def list_projects(self, query: str | None = None, limit: int = 25) -> dict[str, Any]:
        params: dict[str, Any] = {"maxResults": limit}
        if query:
            params["query"] = query
        payload = await self._request("GET", "/rest/api/3/project/search", params=params)
        return {
            "projects": [
                {"key": project.get("key"), "name": project.get("name"), "id": project.get("id")}
                for project in payload.get("values", [])
            ]
        }

    async def find_user(self, query: str, limit: int = 5) -> dict[str, Any]:
        payload = await self._request(
            "GET", "/rest/api/3/user/search", params={"query": query, "maxResults": limit}
        )
        return {
            "users": [
                {
                    "account_id": user.get("accountId"),
                    "display_name": user.get("displayName"),
                    "email": user.get("emailAddress"),
                    "active": user.get("active"),
                }
                for user in payload or []
            ]
        }

    async def list_transitions(self, key: str) -> dict[str, Any]:
        """Which statuses an issue *could* move to. Reading only — the
        assistant cannot perform a transition."""
        payload = await self._request("GET", f"/rest/api/3/issue/{key}/transitions")
        return {
            "key": key,
            "transitions": [
                {"id": item.get("id"), "name": item.get("name"), "to": (item.get("to") or {}).get("name")}
                for item in payload.get("transitions", [])
            ],
        }

    # ----------------------------------------------------------------- agile

    async def list_boards(self, project_key: str | None = None, limit: int = 25) -> dict[str, Any]:
        params: dict[str, Any] = {"maxResults": limit}
        if project_key:
            params["projectKeyOrId"] = project_key
        payload = await self._request("GET", "/rest/agile/1.0/board", params=params)
        return {
            "boards": [
                {
                    "id": board.get("id"),
                    "name": board.get("name"),
                    "type": board.get("type"),
                    "project": (board.get("location") or {}).get("projectKey"),
                }
                for board in payload.get("values", [])
            ]
        }

    async def list_sprints(self, board_id: int, state: str = "active", limit: int = 25) -> dict[str, Any]:
        params: dict[str, Any] = {"maxResults": limit}
        if state and state != "all":
            params["state"] = state
        payload = await self._request(
            "GET", f"/rest/agile/1.0/board/{board_id}/sprint", params=params
        )
        return {
            "board_id": board_id,
            "sprints": [
                {
                    "id": sprint.get("id"),
                    "name": sprint.get("name"),
                    "state": sprint.get("state"),
                    "start": sprint.get("startDate"),
                    "end": sprint.get("endDate"),
                    "goal": sprint.get("goal"),
                }
                for sprint in payload.get("values", [])
            ],
        }

    async def sprint_report(self, sprint_id: int, max_results: int = 100) -> dict[str, Any]:
        """Issues in a sprint, grouped by status and by assignee."""
        payload = await self._request(
            "GET",
            f"/rest/agile/1.0/sprint/{sprint_id}/issue",
            params={"maxResults": max_results, "fields": ",".join(DEFAULT_FIELDS)},
        )
        issues = [self.simplify_issue(issue) for issue in payload.get("issues", [])]

        by_status: dict[str, int] = {}
        by_assignee: dict[str, int] = {}
        for issue in issues:
            by_status[issue.get("status", "Unknown")] = by_status.get(issue.get("status", "Unknown"), 0) + 1
            owner = issue.get("assignee", "Unassigned")
            by_assignee[owner] = by_assignee.get(owner, 0) + 1

        return {
            "sprint_id": sprint_id,
            "total": len(issues),
            "by_status": by_status,
            "by_assignee": by_assignee,
            "issues": issues,
        }
