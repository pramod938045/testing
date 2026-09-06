"""Minimal Jira Cloud REST client — only what the story generator needs."""

from __future__ import annotations

import base64
import re
from typing import Any

import httpx

ISSUE_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]*-\d+$")
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

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
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
