"""The one place that may write to Jira — posting generated test cases back.

Everything else in this package is read-only: `storygen.jira.Jira` refuses any
non-GET call before it is sent, and that guarantee is worth keeping, so the
write path lives here instead of being bolted onto it.

Two safeguards, both deliberate:

* Every entry point takes `confirmed` and raises unless it is True. A caller
  cannot write to Jira by forgetting a flag; it has to say so.
* The default target is a single comment, which one person can delete. Creating
  sub-tasks is opt-in on top of that.

Cloud and Data Center disagree about rich text: v3 wants ADF, v2 wants a wiki
markup string. `body_for` produces whichever the deployment needs.
"""

from __future__ import annotations

from typing import Any

import httpx

from .jira import JiraError, auth_header, detect_deployment


class NotConfirmed(RuntimeError):
    """A write was attempted without explicit confirmation."""


def render_plain(result: dict[str, Any], key: str) -> str:
    """Test cases as plain text with wiki-markup headings.

    Data Center renders this directly. For Cloud, `text_to_adf` turns the same
    text into ADF, so both deployments show the same content.
    """
    cases = result.get("test_cases") or []
    lines = [f"h3. Manual test cases for {key}", ""]

    for case in cases:
        lines.append(f"h4. {case.get('id', '')} — {case.get('title', '')}")
        lines.append(
            f"Type: {case.get('type', '')} | Priority: {case.get('priority', '')}"
        )
        if case.get("covers"):
            lines.append(f"Covers: {case['covers']}")
        for item in case.get("preconditions") or []:
            lines.append(f"- Precondition: {item}")
        if case.get("test_data"):
            lines.append(f"Test data: {case['test_data']}")
        for number, step in enumerate(case.get("steps") or [], start=1):
            lines.append(f"- {number}. {step.get('action', '')} → {step.get('expected', '')}")
        if case.get("expected_result"):
            lines.append(f"Expected result: {case['expected_result']}")
        lines.append("")

    for heading, items in (
        ("Open questions", result.get("open_questions")),
        ("Not manually testable", result.get("not_manually_testable")),
    ):
        if items:
            lines.append(f"h4. {heading}")
            lines += [f"- {item}" for item in items]
            lines.append("")

    lines.append(f"Generated from {key} and reviewed before posting.")
    return "\n".join(lines)


def render_case(case: dict[str, Any]) -> str:
    """One test case as plain text — the body of a sub-task."""
    lines = [f"Type: {case.get('type', '')} | Priority: {case.get('priority', '')}"]
    if case.get("covers"):
        lines.append(f"Covers: {case['covers']}")
    if case.get("preconditions"):
        lines.append("")
        lines.append("Preconditions:")
        lines += [f"- {item}" for item in case["preconditions"]]
    if case.get("test_data"):
        lines += ["", f"Test data: {case['test_data']}"]

    lines += ["", "Steps:"]
    for number, step in enumerate(case.get("steps") or [], start=1):
        lines.append(f"- {number}. {step.get('action', '')} → {step.get('expected', '')}")

    if case.get("expected_result"):
        lines += ["", f"Expected result: {case['expected_result']}"]
    return "\n".join(lines)


def text_to_adf(text: str) -> dict[str, Any]:
    """Wrap plain text as ADF: bullets for "- " lines, paragraphs otherwise.

    Consecutive "- " lines collapse into one bulletList so Cloud renders a
    real list rather than a run of stray paragraphs.
    """
    content: list[dict[str, Any]] = []
    bullets: list[dict[str, Any]] = []

    def flush() -> None:
        if bullets:
            content.append({"type": "bulletList", "content": list(bullets)})
            bullets.clear()

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        if stripped.startswith("- "):
            bullets.append(
                {
                    "type": "listItem",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": stripped[2:]}],
                        }
                    ],
                }
            )
            continue
        flush()
        # "h3. Title" / "h4. Title" become real headings on Cloud.
        level = 3 if stripped.startswith("h3. ") else 4 if stripped.startswith("h4. ") else 0
        if level:
            content.append(
                {
                    "type": "heading",
                    "attrs": {"level": level},
                    "content": [{"type": "text", "text": stripped[4:]}],
                }
            )
        else:
            content.append(
                {"type": "paragraph", "content": [{"type": "text", "text": stripped}]}
            )

    flush()
    if not content:
        content = [{"type": "paragraph", "content": []}]
    return {"type": "doc", "version": 1, "content": content}


class JiraWriter:
    """Writes to Jira. Separate from `Jira`, which cannot and must not."""

    def __init__(
        self,
        base_url: str,
        email: str,
        token: str,
        timeout: float = 30.0,
        deployment: str = "auto",
    ):
        self.base_url = base_url.rstrip("/")
        self.deployment = detect_deployment(self.base_url) if deployment == "auto" else deployment
        self.api = "3" if self.deployment == "cloud" else "2"
        self.http = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={
                "Authorization": auth_header(self.deployment, email, token),
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )

    def close(self) -> None:
        self.http.close()

    def path(self, suffix: str) -> str:
        return f"/rest/api/{self.api}/{suffix.lstrip('/')}"

    def body_for(self, text: str) -> Any:
        """ADF on Cloud, a wiki markup string on Data Center."""
        return text_to_adf(text) if self.deployment == "cloud" else text

    def _post(self, path: str, payload: dict[str, Any]) -> Any:
        try:
            response = self.http.post(path, json=payload)
        except httpx.RequestError as exc:
            raise JiraError(f"Cannot reach {self.base_url}: {exc}") from exc

        if response.is_success:
            return response.json() if response.content else None

        detail = ""
        try:
            data = response.json()
            parts = list(data.get("errorMessages") or [])
            errors = data.get("errors")
            if isinstance(errors, dict):
                parts += [f"{name}: {value}" for name, value in errors.items()]
            detail = "; ".join(parts)
        except ValueError:
            detail = (response.text or "").strip()[:300]

        hint = {
            401: " Check JIRA_API_TOKEN.",
            403: " That account lacks permission to write to this project.",
            404: " Not found — check the issue key.",
        }.get(response.status_code, "")
        raise JiraError(f"Jira {response.status_code}: {detail}{hint}")

    # ----------------------------------------------------------- write paths

    def add_comment(self, key: str, text: str, confirmed: bool = False) -> dict[str, Any]:
        """Post one comment on the issue. Reversible: delete the comment."""
        if not confirmed:
            raise NotConfirmed(
                "add_comment refused: confirmed=False. Writing to Jira needs explicit consent."
            )
        return self._post(self.path(f"issue/{key}/comment"), {"body": self.body_for(text)})

    def create_subtask(
        self,
        parent_key: str,
        project_key: str,
        summary: str,
        description: str,
        issue_type: str = "Sub-task",
        confirmed: bool = False,
    ) -> dict[str, Any]:
        """Create one sub-task under the parent issue.

        Not reversible by this tool — deleting an issue is a Jira permission
        most accounts do not have, so prefer `add_comment` unless you want
        each test case tracked as its own issue.
        """
        if not confirmed:
            raise NotConfirmed(
                "create_subtask refused: confirmed=False. Writing to Jira needs explicit consent."
            )
        fields = {
            "project": {"key": project_key},
            "parent": {"key": parent_key},
            "summary": summary[:255],
            "description": self.body_for(description),
            "issuetype": {"name": issue_type},
        }
        return self._post(self.path("issue"), {"fields": fields})
