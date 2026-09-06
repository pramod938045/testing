"""Pre-flight check: `python -m app.check`.

Run before starting the server so configuration problems arrive as a plain
sentence instead of a stack trace from inside uvicorn.
"""

from __future__ import annotations

import asyncio
import socket
import sys

from .config import ConfigError, settings
from .jira_client import JiraClient, JiraError

PORT = 8000


def port_is_free(port: int = PORT) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1)
        return probe.connect_ex(("127.0.0.1", port)) != 0


async def main() -> int:
    try:
        settings.validate()
    except ConfigError as exc:
        print(f"\n  Settings problem:\n  {exc}\n")
        return 1

    if not settings.anthropic_api_key:
        print("\n  ANTHROPIC_API_KEY is not set in .env.")
        print("  The page will load but every question will fail.")
        print("  Get a key at https://console.anthropic.com/settings/keys\n")
        return 1

    if not port_is_free():
        print(f"\n  Port {PORT} is already in use — something else is running there.")
        print("  Close the other window, or the old chatbot may still be running.")
        print(f"  If the old one is still up, just open http://localhost:{PORT}\n")
        return 1

    jira = JiraClient(
        base_url=settings.jira_base_url,
        email=settings.jira_email,
        api_token=settings.jira_api_token,
        timeout=settings.jira_timeout_seconds,
    )
    try:
        user = await jira.myself()
        print(f"  Jira OK — connected as {user.get('display_name')}")
    except JiraError as exc:
        print(f"\n  Cannot reach Jira:\n  {exc.message}\n")
        return 1
    finally:
        await jira.aclose()

    print("  Settings OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
