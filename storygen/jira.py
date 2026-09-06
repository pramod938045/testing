"""Minimal Jira Cloud REST client — only what the story generator needs."""

from __future__ import annotations

import base64
from typing import Any

import httpx


class JiraError(RuntimeError):
    pass


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
