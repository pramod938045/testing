"""Pre-flight check: `python -m app.check`.

Run before starting the server so configuration problems arrive as a plain
sentence instead of a stack trace from inside uvicorn.
"""

from __future__ import annotations

import asyncio
import os
import socket
from pathlib import Path

from .config import ConfigError, settings
from .jira_client import JiraClient, JiraError

PORT = 8000


def describe_env_file() -> None:
    """Explain what happened to .env — the usual cause of 'I set it but it says missing'.

    Windows Notepad silently saves ".env" as ".env.txt", which python-dotenv
    does not read.
    """
    here = Path.cwd()
    dotenv = here / ".env"

    print(f"  Looking in: {here}")

    if dotenv.is_file():
        keys = []
        for raw in dotenv.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                name, _, value = line.partition("=")
                keys.append((name.strip(), bool(value.strip())))
        print(f"  Found .env with {len(keys)} setting(s):")
        for name, has_value in keys:
            print(f"    {name} = {'(a value)' if has_value else '(EMPTY)'}")
    else:
        print("  No file named exactly '.env' here.")
        lookalikes = sorted(
            p.name for p in here.glob(".env*") if p.is_file() and p.name != ".env"
        ) + sorted(p.name for p in here.glob("env*") if p.is_file())
        if lookalikes:
            print("  But these look close — Windows may have renamed your file:")
            for name in lookalikes:
                print(f"    {name}")
            print("  Rename it to exactly  .env  (no .txt on the end).")

    from_env_var = os.environ.get("ANTHROPIC_API_KEY")
    if from_env_var:
        print(f"  ANTHROPIC_API_KEY is set in this window ({len(from_env_var)} characters).")


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

    key = settings.anthropic_api_key
    if not key:
        print("\n  ANTHROPIC_API_KEY is not set.")
        print("  The page will load but every question will fail.\n")
        describe_env_file()
        print("\n  Quickest fix — set it in this window and re-run the check:")
        print("    set ANTHROPIC_API_KEY=sk-ant-your-key-here")
        print("    python -m app.check")
        print("\n  Get a key at https://console.anthropic.com/settings/keys\n")
        return 1

    if not key.startswith("sk-ant-"):
        print(f"\n  ANTHROPIC_API_KEY is set but looks wrong: it starts with '{key[:6]}'.")
        print("  An Anthropic key starts with 'sk-ant-'.")
        print("  Check you pasted the whole key, with no quotes or spaces around it.\n")
        return 1

    # Catch the example text being pasted verbatim instead of a real key.
    if "your-key" in key or "your_key" in key or "paste" in key.lower():
        print("\n  That is the example text, not a real key:")
        print(f"    {key}")
        print("\n  Replace it with your own key from")
        print("  https://console.anthropic.com/settings/keys")
        print("  A real key is a long random string, about 100 characters.\n")
        return 1

    if len(key) < 40:
        print(f"\n  ANTHROPIC_API_KEY is only {len(key)} characters — too short to be real.")
        print("  A real key is about 100 characters. Copy the whole thing from")
        print("  https://console.anthropic.com/settings/keys\n")
        return 1

    from .ai_health import check_anthropic

    ai = await check_anthropic()
    if ai["anthropic"] != "ok":
        print(f"\n  Anthropic: {ai['anthropic']}")
        if ai.get("fix"):
            print(f"  {ai['fix']}")
        if ai.get("warning"):
            print(f"  {ai['warning']}")
        print(f"\n  .env file in use: {ai['key']['dotenv_file']}\n")
        return 1
    print("  Anthropic OK — key accepted")

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
