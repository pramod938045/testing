#!/usr/bin/env python3
"""Terminal client for the QA agent (Jira + TestRail).

    python -m qa_agent.main
    python -m qa_agent.main "which TestRail cases cover ABC-123, and did they pass?"

When the agent wants to add test cases, every case is printed and you are
asked to approve the batch. Anything but "y" creates nothing.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from app.config import ConfigError, settings
from app.jira_client import JiraClient, JiraError

from .agent import QAAgent
from .config import TestRailSettings
from .testrail_client import TestRailClient, TestRailError

BOLD, DIM, YELLOW, RESET = "\033[1m", "\033[2m", "\033[33m", "\033[0m"


def format_proposal(proposal: dict[str, Any]) -> str:
    section = proposal["section"]
    lines = [
        f"{YELLOW}{BOLD}Create {len(proposal['cases'])} TestRail case(s)"
        f" in section \"{section.get('name')}\" (id {section.get('id')}),"
        f" referencing {proposal['jira_key']}:{RESET}"
    ]
    for number, case in enumerate(proposal["cases"], 1):
        lines.append(f"\n  {BOLD}{number}. {case['title']}{RESET}")
        if case.get("preconditions"):
            lines.append(f"     Preconditions: {case['preconditions']}")
        for step_no, step in enumerate(case.get("steps") or [], 1):
            lines.append(f"     {step_no}) {step['action']}")
            lines.append(f"        {DIM}expected: {step['expected']}{RESET}")
    return "\n".join(lines)


async def ask_to_confirm(proposal: dict[str, Any]) -> bool:
    print("\n" + format_proposal(proposal) + "\n")
    try:
        answer = await asyncio.to_thread(input, f"{BOLD}Create these in TestRail? [y/N] {RESET}")
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes"}


async def main() -> int:
    testrail_settings = TestRailSettings()
    try:
        settings.validate()
        testrail_settings.validate()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    jira = JiraClient(
        base_url=settings.jira_base_url,
        email=settings.jira_email,
        api_token=settings.jira_api_token,
        timeout=settings.jira_timeout_seconds,
        deployment=settings.jira_deployment,
    )
    testrail = TestRailClient(
        base_url=testrail_settings.base_url,
        email=testrail_settings.email,
        api_key=testrail_settings.api_key,
        timeout=testrail_settings.timeout_seconds,
        template_id=testrail_settings.template_id,
        steps_format=testrail_settings.steps_format,
    )
    try:
        try:
            user = await jira.myself()
        except JiraError as exc:
            print(f"Could not connect to Jira: {exc.message}", file=sys.stderr)
            return 1
        try:
            projects = (await testrail.list_projects())["projects"]
        except TestRailError as exc:
            print(f"Could not connect to TestRail: {exc.message}", file=sys.stderr)
            return 1

        agent = QAAgent(
            jira=jira,
            testrail=testrail,
            settings=settings,
            testrail_settings=testrail_settings,
            confirm=ask_to_confirm,
            jira_user=user,
        )
        one_shot = " ".join(sys.argv[1:]).strip()

        print(f"{BOLD}QA agent{RESET} — Jira {settings.jira_base_url} as {user.get('display_name')}, "
              f"TestRail {testrail_settings.base_url} ({len(projects)} projects)")
        print(f"{DIM}Jira is read-only; new TestRail cases are created only after you approve them{RESET}")
        if not one_shot:
            print(f"{DIM}Ctrl-C or 'exit' to quit, '/reset' to clear the conversation.{RESET}\n")

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
        await testrail.aclose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
