#!/usr/bin/env python3
"""Terminal chat client — the same agent as the web app, without the browser.

    python cli.py
    python cli.py "what's assigned to me this sprint?"
"""

from __future__ import annotations

import asyncio
import sys

from app.agent import JiraChatAgent
from app.config import ConfigError, settings
from app.jira_client import JiraClient, JiraError

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"


async def main() -> int:
    try:
        settings.validate()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    jira = JiraClient(
        base_url=settings.jira_base_url,
        email=settings.jira_email,
        api_token=settings.jira_api_token,
        timeout=settings.jira_timeout_seconds,
    )
    try:
        user = await jira.myself()
    except JiraError as exc:
        print(f"Could not connect to Jira: {exc.message}", file=sys.stderr)
        await jira.aclose()
        return 1

    agent = JiraChatAgent(jira=jira, settings=settings, jira_user=user)
    one_shot = " ".join(sys.argv[1:]).strip()

    print(f"{BOLD}Jira chatbot{RESET} — {settings.jira_base_url} as {user.get('display_name')}")
    print(f"{DIM}read-only — this assistant cannot change Jira{RESET}")
    if not one_shot:
        print(f"{DIM}Ctrl-C or 'exit' to quit, '/reset' to clear the conversation.{RESET}\n")

    try:
        while True:
            if one_shot:
                question = one_shot
            else:
                try:
                    question = input(f"{BOLD}you >{RESET} ").strip()
                except EOFError:
                    break
                if question.lower() in {"exit", "quit"}:
                    break
                if question == "/reset":
                    agent.reset()
                    print(f"{DIM}conversation cleared{RESET}\n")
                    continue
                if not question:
                    continue

            result = await agent.chat(question)
            for call in result.tool_calls:
                mark = "·" if call.ok else "✗"
                print(f"{DIM}  {mark} {call.name}({call.args}){RESET}")
            print(f"\n{result.reply}\n")

            if one_shot:
                break
    except KeyboardInterrupt:
        print()
    finally:
        await jira.aclose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
