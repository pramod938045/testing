"""Async TestRail API v2 client.

TestRail puts the API route in the query string — `index.php?/api/v2/get_case/42`
— and extra filters follow with `&`, so URLs are built by hand here rather than
with httpx `params`, which would mangle that shape.

ONE WRITE, AND ONLY ONE. `_request` refuses every POST except `add_case`, so
nothing can be updated, closed or deleted, and no runs or results can be added.
Creating a case is additionally gated by the agent, which asks a person to
approve each batch before this client is called (see `qa_agent/agent.py`).

Responses are trimmed to the fields that matter, like the Jira client.
"""

from __future__ import annotations

import base64
from typing import Any
from urllib.parse import urlencode

import httpx

# The only POST endpoint this client will send.
WRITE_ENDPOINTS = ("add_case",)

# TestRail's built-in result statuses. Sites can add custom ones (id 6+).
STATUS_NAMES = {1: "Passed", 2: "Blocked", 3: "Untested", 4: "Retest", 5: "Failed"}

# Stop following pages after this many items so a huge suite cannot flood the context.
MAX_ITEMS = 250


class TestRailError(RuntimeError):
    """A TestRail API call failed. `message` is safe to show to the user/model."""

    __test__ = False  # not a pytest test class, despite the name

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def status_name(status_id: Any) -> str:
    if status_id is None:
        return "Untested"
    return STATUS_NAMES.get(status_id, f"Custom status {status_id}")


def _has_ref(case: dict[str, Any], ref: str) -> bool:
    refs = case.get("refs") or ""
    return ref.upper() in {part.strip().upper() for part in refs.split(",") if part.strip()}


class TestRailClient:
    __test__ = False

    def __init__(
        self,
        base_url: str,
        email: str,
        api_key: str,
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
        template_id: int | None = None,
        steps_format: str = "separated",
    ):
        """`steps_format`: "separated" sends `custom_steps_separated` (the
        "Test Case (Steps)" template); "text" sends `custom_steps` and
        `custom_expected` (the "Test Case (Text)" template)."""
        self.base_url = base_url.rstrip("/")
        self.template_id = template_id
        self.steps_format = steps_format
        token = base64.b64encode(f"{email}:{api_key}".encode()).decode()
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
    def url(endpoint: str, **filters: Any) -> str:
        query = urlencode({k: v for k, v in filters.items() if v not in (None, "")})
        return f"/index.php?/api/v2/{endpoint}" + (f"&{query}" if query else "")

    @staticmethod
    def _refuse_writes(method: str, endpoint: str) -> None:
        """Block every write except creating a test case, before anything is sent."""
        method = method.upper()
        if method == "GET":
            return
        name = endpoint.split("/", 1)[0]
        if method == "POST" and name in WRITE_ENDPOINTS:
            return
        raise TestRailError(
            f"Blocked: {method} {name} would change TestRail. This agent can only read "
            "TestRail and add new test cases."
        )

    async def _request(self, method: str, endpoint: str, *, json: Any = None, **filters: Any) -> Any:
        self._refuse_writes(method, endpoint)
        try:
            response = await self._client.request(method, self.url(endpoint, **filters), json=json)
        except httpx.RequestError as exc:
            raise TestRailError(f"Could not reach TestRail at {self.base_url}: {exc}") from exc

        if response.is_success:
            if not response.content:
                return None
            try:
                return response.json()
            except ValueError:
                return response.text
        raise TestRailError(self._error_message(response), response.status_code)

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            detail = str((response.json() or {}).get("error") or "")
        except (ValueError, AttributeError):
            detail = ""
        detail = detail or (response.text or "").strip()[:300]
        hints = {
            401: "Check TESTRAIL_EMAIL and TESTRAIL_API_KEY in .env.",
            403: "The TestRail account lacks permission for this, or the API is disabled "
                 "(Administration -> Site Settings -> API).",
            429: "TestRail is rate limiting requests; try again shortly.",
        }
        return f"TestRail returned {response.status_code}. {detail} {hints.get(response.status_code, '')}".strip()

    async def _paged(self, endpoint: str, key: str, **filters: Any) -> list[dict[str, Any]]:
        """Collect a list endpoint. TestRail 6.7+ wraps lists in a page object
        with `_links.next`; older versions return a bare list."""
        items: list[dict[str, Any]] = []
        offset = 0
        while True:
            payload = await self._request("GET", endpoint, offset=offset or None, **filters)
            if isinstance(payload, list):
                return (items + payload)[:MAX_ITEMS]
            page = (payload or {}).get(key) or []
            items += page
            if not page or not ((payload.get("_links") or {}).get("next")) or len(items) >= MAX_ITEMS:
                return items[:MAX_ITEMS]
            offset += len(page)

    # ---------------------------------------------------------------- shaping

    def case_url(self, case_id: Any) -> str:
        return f"{self.base_url}/index.php?/cases/view/{case_id}"

    def run_url(self, run_id: Any) -> str:
        return f"{self.base_url}/index.php?/runs/view/{run_id}"

    def _case(self, case: dict[str, Any], detail: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": case.get("id"),
            "title": case.get("title"),
            "section_id": case.get("section_id"),
            "suite_id": case.get("suite_id"),
            "refs": case.get("refs"),
            "priority_id": case.get("priority_id"),
            "type_id": case.get("type_id"),
            "url": self.case_url(case.get("id")),
        }
        if detail:
            data["preconditions"] = case.get("custom_preconds")
            steps = case.get("custom_steps_separated")
            if steps:
                data["steps"] = [
                    {"action": step.get("content"), "expected": step.get("expected")} for step in steps
                ]
            else:
                data["steps_text"] = case.get("custom_steps")
                data["expected_text"] = case.get("custom_expected")
        return {k: v for k, v in data.items() if v not in (None, "", [])}

    # ------------------------------------------------------------------ read

    async def list_projects(self) -> dict[str, Any]:
        projects = await self._paged("get_projects", "projects", is_completed=0)
        return {
            "projects": [
                {"id": p.get("id"), "name": p.get("name"), "suite_mode": p.get("suite_mode")}
                for p in projects
            ]
        }

    async def list_suites(self, project_id: int) -> dict[str, Any]:
        payload = await self._request("GET", f"get_suites/{project_id}")
        suites = payload if isinstance(payload, list) else (payload or {}).get("suites", [])
        return {"project_id": project_id, "suites": [{"id": s.get("id"), "name": s.get("name")} for s in suites]}

    async def list_sections(self, project_id: int, suite_id: int | None = None) -> dict[str, Any]:
        sections = await self._paged(f"get_sections/{project_id}", "sections", suite_id=suite_id)
        return {
            "project_id": project_id,
            "sections": [
                {"id": s.get("id"), "name": s.get("name"), "parent_id": s.get("parent_id"), "depth": s.get("depth")}
                for s in sections
            ],
        }

    async def get_section(self, section_id: int) -> dict[str, Any]:
        section = await self._request("GET", f"get_section/{section_id}")
        return {"id": section.get("id"), "name": section.get("name"), "suite_id": section.get("suite_id")}

    async def find_cases(
        self,
        project_id: int,
        suite_id: int | None = None,
        section_id: int | None = None,
        ref: str | None = None,
    ) -> dict[str, Any]:
        """Cases in a project, optionally only those referencing a Jira key.

        The `refs` filter is not available on every TestRail version, so on a
        400 the search is repeated without it, and the match is always
        re-checked locally (refs is a comma-separated string).
        """
        filters = {"suite_id": suite_id, "section_id": section_id}
        try:
            cases = await self._paged(f"get_cases/{project_id}", "cases", refs=ref, **filters)
        except TestRailError as exc:
            if not ref or exc.status_code != 400:
                raise
            cases = await self._paged(f"get_cases/{project_id}", "cases", **filters)
        if ref:
            cases = [case for case in cases if _has_ref(case, ref)]
        return {"project_id": project_id, "ref": ref, "count": len(cases), "cases": [self._case(c) for c in cases]}

    async def get_case(self, case_id: int) -> dict[str, Any]:
        return self._case(await self._request("GET", f"get_case/{case_id}"), detail=True)

    async def list_runs(self, project_id: int, is_completed: bool | None = None) -> dict[str, Any]:
        completed = None if is_completed is None else int(is_completed)
        runs = await self._paged(f"get_runs/{project_id}", "runs", is_completed=completed)
        return {
            "project_id": project_id,
            "runs": [
                {
                    "id": r.get("id"),
                    "name": r.get("name"),
                    "is_completed": r.get("is_completed"),
                    "passed": r.get("passed_count"),
                    "failed": r.get("failed_count"),
                    "blocked": r.get("blocked_count"),
                    "untested": r.get("untested_count"),
                    "retest": r.get("retest_count"),
                    "refs": r.get("refs"),
                    "url": self.run_url(r.get("id")),
                }
                for r in runs
            ],
        }

    async def get_run_tests(self, run_id: int) -> dict[str, Any]:
        """Every test in a run with its current status, plus counts by status."""
        tests = await self._paged(f"get_tests/{run_id}", "tests")
        shaped = [
            {
                "test_id": t.get("id"),
                "case_id": t.get("case_id"),
                "title": t.get("title"),
                "status": status_name(t.get("status_id")),
                "refs": t.get("refs"),
            }
            for t in tests
        ]
        by_status: dict[str, int] = {}
        for test in shaped:
            by_status[test["status"]] = by_status.get(test["status"], 0) + 1
        return {"run_id": run_id, "total": len(shaped), "by_status": by_status, "tests": shaped, "url": self.run_url(run_id)}

    # ----------------------------------------------------------------- write

    def case_body(self, case: dict[str, Any], jira_key: str | None) -> dict[str, Any]:
        steps = case.get("steps") or []
        body: dict[str, Any] = {"title": case["title"]}
        if jira_key:
            body["refs"] = jira_key
        if case.get("preconditions"):
            body["custom_preconds"] = case["preconditions"]
        if self.template_id:
            body["template_id"] = self.template_id
        if self.steps_format == "text":
            body["custom_steps"] = "\n".join(f"{n}. {s['action']}" for n, s in enumerate(steps, 1))
            body["custom_expected"] = "\n".join(f"{n}. {s['expected']}" for n, s in enumerate(steps, 1))
        else:
            body["custom_steps_separated"] = [{"content": s["action"], "expected": s["expected"]} for s in steps]
        return body

    async def add_case(self, section_id: int, case: dict[str, Any], jira_key: str | None = None) -> dict[str, Any]:
        created = await self._request("POST", f"add_case/{section_id}", json=self.case_body(case, jira_key))
        return {"id": created.get("id"), "title": created.get("title"), "url": self.case_url(created.get("id"))}
