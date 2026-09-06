#!/usr/bin/env python3
"""Slack front end for the Jira chatbot.

Same `JiraChatAgent` as the web app — only the transport differs. Runs over
Socket Mode, so it needs no public URL and no inbound firewall rules.

    python slack_bot.py

Each Slack thread is its own conversation; a DM without a thread is one
conversation per user.
"""

from __future__ import annotations

import asyncio
import logging
import re

from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

from app.agent import JiraChatAgent
from app.config import ConfigError, settings
from app.jira_client import JiraClient, JiraError
from app.sessions import SessionStore
from app.slack_format import clean_mention_text, to_mrkdwn, tool_context, truncate

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("jira-slack-bot")

SLACK_STYLE = (
    "You are replying in Slack. Slack cannot render Markdown tables or headings: use short "
    "bullet lists instead of tables, and keep replies under ~15 lines. Write links as "
    "[text](url) and bold as **text** — they are converted to Slack formatting for you."
)
RESET_WORDS = {"reset", "/reset", "new chat", "start over", "forget it"}

state: dict = {"jira": None, "jira_user": {}}
sessions: SessionStore[JiraChatAgent] = SessionStore(
    factory=lambda: JiraChatAgent(
        jira=state["jira"],
        settings=settings,
        jira_user=state["jira_user"],
        extra_instructions=SLACK_STYLE,
    )
)

def session_key(event: dict) -> str:
    """One conversation per thread; DMs without a thread are per user."""
    channel = event.get("channel", "")
    thread = event.get("thread_ts") or event.get("ts", "")
    if event.get("channel_type") == "im" and not event.get("thread_ts"):
        return f"dm:{channel}:{event.get('user', '')}"
    return f"{channel}:{thread}"


async def respond(event: dict, client) -> None:
    text = clean_mention_text(event.get("text", ""))
    channel = event["channel"]
    # Reply in-thread when mentioned in a channel; keep DMs flat unless already threaded.
    thread_ts = event.get("thread_ts") or (
        None if event.get("channel_type") == "im" else event.get("ts")
    )
    key = session_key(event)

    if not text:
        await client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text="Ask me something about Jira — try *what's assigned to me and not done?*",
        )
        return

    if text.lower().strip(" .!") in RESET_WORDS:
        sessions.pop(key)
        await client.chat_postMessage(
            channel=channel, thread_ts=thread_ts, text="Started a fresh conversation. :broom:"
        )
        return

    placeholder = await client.chat_postMessage(
        channel=channel, thread_ts=thread_ts, text="_Checking Jira…_"
    )

    try:
        result = await sessions.get(key).chat(text)
        reply = truncate(to_mrkdwn(result.reply)) or "_(no answer)_"
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": reply}}]
        context = tool_context(result.tool_calls)
        if context:
            blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": context}]})
    except Exception:
        logger.exception("Failed to answer message in %s", key)
        reply = ":warning: Something went wrong answering that. The server log has the detail."
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": reply}}]

    await client.chat_update(
        channel=channel, ts=placeholder["ts"], text=re.sub(r"[*_`]", "", reply)[:300], blocks=blocks
    )


def is_direct_question(event: dict) -> bool:
    """True for a DM a human actually typed.

    Channel traffic reaches us via `app_mention` instead, and bot posts, edits
    and channel-join notices are not questions.
    """
    if event.get("channel_type") != "im":
        return False
    return not (event.get("bot_id") or event.get("subtype"))


def build_app() -> AsyncApp:
    """Construct the Bolt app and register handlers.

    Built here rather than at import time so the module can be imported (and
    tested) without Slack credentials.
    """
    slack_app = AsyncApp(token=settings.slack_bot_token)

    @slack_app.event("app_mention")
    async def handle_mention(event, client) -> None:
        await respond(event, client)

    @slack_app.event("message")
    async def handle_message(event, client) -> None:
        if is_direct_question(event):
            await respond(event, client)

    return slack_app


async def main() -> int:
    try:
        settings.validate_slack()
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        return 1

    jira = JiraClient(
        base_url=settings.jira_base_url,
        email=settings.jira_email,
        api_token=settings.jira_api_token,
        timeout=settings.jira_timeout_seconds,
    )
    state["jira"] = jira
    try:
        state["jira_user"] = await jira.myself()
    except JiraError as exc:
        logger.error("Could not connect to Jira: %s", exc.message)
        await jira.aclose()
        return 1

    logger.info(
        "Starting Slack bot — Jira %s as %s%s",
        settings.jira_base_url,
        state["jira_user"].get("display_name"),
        "" if settings.allow_writes else " (read-only)",
    )
    handler = AsyncSocketModeHandler(build_app(), settings.slack_app_token)
    try:
        await handler.start_async()
    finally:
        await jira.aclose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
