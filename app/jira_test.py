"""Safe Jira connection test: `python -m app.jira_test [ISSUE-KEY]`.

Answers, in order, the questions that a 404 leaves ambiguous:

  1. which base URL is actually in use
  2. which authentication scheme is being sent
  3. which REST API version the paths use
  4. what the "current user" endpoint says
  5. what the "get issue" endpoint says for a given key
  6. whether a failure is the URL, the key, or permissions

It prints the HTTP status code and Jira's own message. It never prints the
token — only whether one is set and how long it is — and it only ever issues
GET requests, so it cannot change anything.
"""

from __future__ import annotations

import asyncio
import sys

import httpx

from .config import ConfigError, settings
from .jira_client import JiraClient

DEFAULT_KEY = "UPAMCORE-30728"


def _safe_body(response: httpx.Response, limit: int = 300) -> str:
    """Jira's own error text, without dumping an HTML error page."""
    try:
        payload = response.json()
    except ValueError:
        text = (response.text or "").strip()
        if text.lstrip().startswith("<"):
            return "(an HTML page, not JSON — often a login page or a proxy/WAF)"
        return text[:limit]

    if isinstance(payload, dict):
        parts = list(payload.get("errorMessages") or [])
        errors = payload.get("errors")
        if isinstance(errors, dict):
            parts += [f"{k}: {v}" for k, v in errors.items()]
        if not parts and payload.get("message"):
            parts.append(str(payload["message"]))
        if parts:
            return "; ".join(parts)[:limit]
    return ""


def _meaning(status: int, what: str) -> str:
    return {
        200: "",
        401: "not authenticated — the token is wrong, expired, or not sent as Bearer",
        403: "authenticated but refused — permissions, or a WAF/proxy in front of Jira",
        404: f"{what} does not exist on THIS site, or the account cannot see it",
    }.get(status, "unexpected status")


async def probe(jira: JiraClient, method: str, path: str) -> tuple[int, str]:
    """One GET, returning the status code and a safe message."""
    try:
        response = await jira._client.request(method, path)
    except httpx.RequestError as exc:
        return 0, f"could not connect: {type(exc).__name__} — VPN, DNS or TLS"
    return response.status_code, _safe_body(response)


async def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    key = (argv[0] if argv else DEFAULT_KEY).strip().upper()

    try:
        settings.validate()
    except ConfigError as exc:
        print(f"\nSettings problem: {exc}\n")
        print("Fix with:  python -m app.setup\n")
        return 1

    jira = JiraClient(
        base_url=settings.jira_base_url,
        email=settings.jira_email,
        api_token=settings.jira_api_token,
        timeout=settings.jira_timeout_seconds,
        deployment=settings.jira_deployment,
    )
    scheme = jira._client.headers.get("Authorization", " ").split()[0]
    token_length = len(settings.jira_api_token)

    print("\nJira connection test")
    print(f"  1. Base URL     : {jira.base_url}")
    print(f"  2. Auth scheme  : {scheme}  (token: set, {token_length} characters — never shown)")
    print(f"  3. Deployment   : {jira.deployment}   API version: {jira.api}")
    print(f"     Email        : {settings.jira_email or '(unused on Data Center)'}")
    print(f"  4. Issue key    : {key}")

    if jira.deployment == "server" and scheme != "Bearer":
        print("\n  WRONG AUTH: Data Center needs a Bearer personal access token.")
        print("  Set JIRA_DEPLOYMENT=server in .env, or check the site URL.\n")
        await jira.aclose()
        return 1

    print()
    failures = 0

    # --- 5a. current user ---------------------------------------------------
    path = jira.path("myself")
    status, body = await probe(jira, "GET", path)
    note = _meaning(status, "the endpoint")
    print(f"  GET {path}")
    print(f"      -> {status or 'no response'}  {note or 'authenticated'}")
    if body:
        print(f"      Jira says: {body}")
    if status != 200:
        failures += 1

    # --- 5b. the issue itself ----------------------------------------------
    path = jira.path(f"issue/{key}")
    status, body = await probe(jira, "GET", path)
    print(f"\n  GET {path}")
    print(f"      -> {status or 'no response'}  {_meaning(status, key) or 'found'}")
    if body:
        print(f"      Jira says: {body}")

    # --- 6. is a 404 the key, or the whole project? -------------------------
    if status == 404:
        prefix = key.split("-")[0]
        project_path = jira.path(f"project/{prefix}")
        project_status, project_body = await probe(jira, "GET", project_path)
        print(f"\n  GET {project_path}")
        print(f"      -> {project_status}  {'project exists' if project_status == 200 else 'project not visible here'}")
        if project_status == 200:
            print(f"\n  Diagnosis: project {prefix} IS on this site, so the issue number is")
            print("  wrong, the issue was deleted or moved, or you lack permission for it.")
        else:
            print(f"\n  Diagnosis: project {prefix} is not visible on {jira.base_url}.")
            print("  Either this is the wrong Jira site for that key, or your account")
            print("  has no access to that project.")
        failures += 1
    elif status != 200:
        failures += 1

    await jira.aclose()

    if failures:
        print("\n  Result: NOT working — see the diagnosis above.\n")
        return 1
    print("\n  Result: Jira authentication and retrieval are working.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
