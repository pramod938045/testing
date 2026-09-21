"""Minimal Jira Cloud REST client — only what the story generator needs.

READ-ONLY BY DESIGN. `_request` refuses any call that could change Jira data:
only GET, and POST to the two search endpoints (search is a read that happens
to use POST), are allowed. Nothing here can create, edit, transition or delete
an issue, and a future edit that tried to would raise instead of writing.
"""

from __future__ import annotations

import base64
import re
from typing import Any

import httpx

from .adf import to_text

ISSUE_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]*-\d+$")
# The only non-GET calls allowed: searching reads data but uses POST.
SEARCH_PATHS = ("/rest/api/3/search/jql", "/rest/api/3/search")
CONTEXT_FIELDS = [
    "summary",
    "description",
    "status",
    "issuetype",
    "project",
    "labels",
    "priority",
    "issuelinks",
    "subtasks",
]
# Issue types worth generating stories from. "Change Request" doesn't exist on
# every site, so a search that mentions it falls back to searching all types.
PARENT_TYPES = ("Epic", "Change Request")


class JiraError(RuntimeError):
    pass


def looks_like_key(text: str) -> bool:
    return bool(ISSUE_KEY.match(text.strip()))


def escape_jql(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


class Jira:
    def __init__(self, base_url: str, email: str, token: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        auth = base64.b64encode(f"{email}:{token}".encode()).decode()
        self.http = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={
                "Authorization": f"Basic {auth}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )

    def close(self) -> None:
        self.http.close()

    @staticmethod
    def _refuse_writes(method: str, path: str) -> None:
        """Block anything that could modify Jira.

        Searching uses POST but only reads, so those two paths are allowed.
        Every other non-GET call raises before a request is sent.
        """
        method = method.upper()
        if method == "GET":
            return
        if method == "POST" and path in SEARCH_PATHS:
            return
        raise JiraError(
            f"Blocked: {method} {path} would modify Jira. This tool is read-only "
            "and never creates, edits or deletes anything."
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        self._refuse_writes(method, path)
        try:
            response = self.http.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            raise JiraError(f"Cannot reach {self.base_url}: {exc}") from exc

        if response.is_success:
            return response.json() if response.content else None

        detail = ""
        try:
            payload = response.json()
            detail = "; ".join(payload.get("errorMessages") or []) or str(payload.get("errors", ""))
        except ValueError:
            detail = response.text[:200]
        hint = {
            401: " Check JIRA_EMAIL and JIRA_API_TOKEN.",
            403: " That account lacks permission.",
            404: " Not found — check the key.",
        }.get(response.status_code, "")
        raise JiraError(f"Jira {response.status_code}: {detail}{hint}")

    def myself(self) -> dict[str, Any]:
        """Who the API token authenticates as. Used by the connection test."""
        return self._request("GET", "/rest/api/3/myself")

    def search(self, jql: str, fields: list[str], limit: int = 20) -> list[dict[str, Any]]:
        body = {"jql": jql, "maxResults": limit, "fields": fields}
        try:
            payload = self._request("POST", "/rest/api/3/search/jql", json=body)
        except JiraError as exc:
            # Older sites only have the legacy search endpoint.
            if "404" not in str(exc) and "410" not in str(exc):
                raise
            payload = self._request("POST", "/rest/api/3/search", json=body)
        return payload.get("issues", [])

    def get_issue(self, key: str, fields: list[str]) -> dict[str, Any]:
        return self._request(
            "GET", f"/rest/api/3/issue/{key}", params={"fields": ",".join(fields)}
        )

    def get_context(self, key: str, description_limit: int = 6000) -> dict[str, Any]:
        """Everything the AI needs about one Epic / Change Request.

        Linked issues carry their own summary and status in the same response,
        so no extra requests are needed.
        """
        issue = self.get_issue(key, CONTEXT_FIELDS)
        fields = issue.get("fields") or {}

        def name(value: Any, attr: str = "name") -> str:
            return (value or {}).get(attr, "") if isinstance(value, dict) else ""

        links = []
        for link in fields.get("issuelinks") or []:
            other = link.get("outwardIssue") or link.get("inwardIssue")
            if not other:
                continue
            relation = (
                link["type"].get("outward" if link.get("outwardIssue") else "inward", "relates to")
                if link.get("type")
                else "relates to"
            )
            other_fields = other.get("fields") or {}
            links.append(
                {
                    "relation": relation,
                    "key": other.get("key"),
                    "summary": other_fields.get("summary", ""),
                    "status": name(other_fields.get("status")),
                    "type": name(other_fields.get("issuetype")),
                }
            )

        return {
            "key": issue.get("key"),
            "summary": fields.get("summary", ""),
            "type": name(fields.get("issuetype")),
            "status": name(fields.get("status")),
            "project": name(fields.get("project"), "key"),
            "priority": name(fields.get("priority")),
            "labels": fields.get("labels") or [],
            "description": to_text(fields.get("description"), description_limit),
            "links": links,
            "subtasks": [
                {"key": sub.get("key"), "summary": (sub.get("fields") or {}).get("summary", "")}
                for sub in fields.get("subtasks") or []
            ],
            "url": f"{self.base_url}/browse/{issue.get('key')}",
        }

    def find_parents(self, query: str, limit: int = 20) -> tuple[list[dict[str, Any]], bool]:
        """Find candidate Epics / Change Requests by text.

        Returns `(issues, type_filtered)`. `type_filtered` is False when the
        site has no such issue types and the search had to cover all types.
        """
        text = escape_jql(query)
        types = ", ".join(f'"{name}"' for name in PARENT_TYPES)
        where = f'(summary ~ "{text}" OR description ~ "{text}")'
        fields = ["summary", "status", "issuetype", "updated"]

        try:
            jql = f"issuetype in ({types}) AND {where} ORDER BY updated DESC"
            return self.search(jql, fields, limit), True
        except JiraError as exc:
            if "issuetype" not in str(exc) and "does not exist" not in str(exc):
                raise
            jql = f"{where} ORDER BY updated DESC"
            return self.search(jql, fields, limit), False
