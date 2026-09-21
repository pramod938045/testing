"""Sample data for demo mode — no Jira, no API key.

Lets the whole flow be shown end to end when there is no Anthropic credit, or
when demonstrating the tool to someone who has no Jira access. Nothing here
touches the real Jira client; `main.py` simply reads from here instead.

The issues match the shape `Jira.get_context()` returns, and the suggestions
match the shape `generate_stories()` returns, so the same rendering code runs.
"""

from __future__ import annotations

from typing import Any

DEMO_SITE = "https://demo.atlassian.net"

# Written in the house style seen in real Change Requests: a "Story:" line,
# a "Details:" list, then "Additional Notes:".
DEMO_ISSUES: dict[str, dict[str, Any]] = {
    "DEMO-1": {
        "key": "DEMO-1",
        "summary": "Self-service account closure",
        "type": "Epic",
        "status": "In Progress",
        "project": "DEMO",
        "priority": "Major",
        "labels": ["compliance", "player-account"],
        "description": (
            "Story:\n\n"
            "As a player, I would like to close my own account from the website, so that I do "
            "not have to contact support and wait for a reply.\n\n"
            "Details:\n\n"
            "- Closure must be reachable from Account Settings on web and mobile web\n"
            "- The player must confirm in a second step before anything happens\n"
            "- Any remaining balance must be withdrawn before closure can proceed\n"
            "- A player with an open bonus must be warned that the bonus will be forfeited\n"
            "- Send a confirmation email once the account is closed\n"
            "- Closed accounts must not be able to log in, deposit or receive marketing\n\n"
            "Additional Notes:\n\n"
            "- Regulatory requirement in the Ontario market, deadline end of Q4\n"
            "- Reopening a closed account stays a support-only operation for now"
        ),
        "links": [
            {
                "relation": "blocks",
                "key": "DEMO-14",
                "summary": "Marketing preferences service migration",
                "status": "In Review",
                "type": "Change Request",
            }
        ],
        "subtasks": [
            {"key": "DEMO-2", "summary": "Add 'Close account' entry to Account Settings"}
        ],
        "url": f"{DEMO_SITE}/browse/DEMO-1",
    },
    "DEMO-7": {
        "key": "DEMO-7",
        "summary": "First time deposit tracking (Web - analytics)",
        "type": "Change Request",
        "status": "New",
        "project": "DEMO",
        "priority": "Medium",
        "labels": ["analytics"],
        "description": (
            "Story:\n\n"
            "As a web-focused marketing specialist, I would like to learn how many players made "
            "a first-time deposit after completing registration, so that I can see which "
            "channels bring players who actually fund an account.\n\n"
            "Details:\n\n"
            "- Capture successful deposit events and send them to the analytics platform\n"
            "  - Amount of the deposit\n"
            "  - Currency of the deposit if available\n"
            "  - Type of payment used if available\n"
            "- Event name example: first_time_deposit\n"
            "- Deposit failures must not be captured\n"
            "- Only the player's first successful deposit counts\n"
            "- Verify the values recorded in the analytics platform match the payment records\n\n"
            "Additional Notes:\n\n"
            "- Web only for this change; the mobile app is tracked separately"
        ),
        "links": [],
        "subtasks": [],
        "url": f"{DEMO_SITE}/browse/DEMO-7",
    },
}

# What a good model returns for the issues above. Used verbatim in demo mode.
DEMO_STORIES: dict[str, dict[str, Any]] = {
    "DEMO-1": {
        "stories": [
            {
                "summary": "Add account closure entry point to Account Settings",
                "user_story": (
                    "As a player, I would like to find a clear way to close my account in "
                    "Account Settings, so that I do not have to contact support."
                ),
                "details": (
                    "Add a 'Close account' option to Account Settings on web and mobile web. "
                    "Selecting it starts the closure flow; it does not close anything by itself."
                ),
                "acceptance_criteria": [
                    "'Close account' is visible in Account Settings on web and on mobile web",
                    "Selecting it opens the closure flow and changes nothing on its own",
                    "The option is not shown to an already-closed account",
                ],
            },
            {
                "summary": "Require confirmation before closing an account",
                "user_story": (
                    "As a player, I would like to confirm before my account is closed, so that "
                    "I cannot lose access by mistake."
                ),
                "details": (
                    "A second confirmation step states what closure means and requires an "
                    "explicit action. Abandoning the flow leaves the account untouched."
                ),
                "acceptance_criteria": [
                    "Closure only proceeds after an explicit confirmation action",
                    "Leaving the flow at any point leaves the account open",
                    "The confirmation states that login, deposits and marketing will stop",
                ],
            },
            {
                "summary": "Block closure while a balance remains",
                "user_story": (
                    "As a player, I would like to be told to withdraw my balance first, so that "
                    "I do not lose funds by closing my account."
                ),
                "details": (
                    "Check the balance when the flow starts. A non-zero balance stops closure "
                    "and directs the player to withdrawal."
                ),
                "acceptance_criteria": [
                    "A player with a non-zero balance cannot complete closure",
                    "The message states the remaining amount and links to withdrawal",
                    "A player with a zero balance is not shown the message",
                ],
            },
            {
                "summary": "Warn about bonus forfeiture before closure",
                "user_story": (
                    "As a player with an open bonus, I would like to be warned that closing "
                    "forfeits it, so that I can decide knowingly."
                ),
                "details": "Detect any open bonus and show a warning in the confirmation step.",
                "acceptance_criteria": [
                    "A player with an open bonus sees a forfeiture warning before confirming",
                    "A player with no open bonus does not see the warning",
                    "Confirming after the warning closes the account and forfeits the bonus",
                ],
            },
            {
                "summary": "Send a confirmation email on closure",
                "user_story": (
                    "As a player, I would like an email confirming my account is closed, so "
                    "that I have a record of it."
                ),
                "details": "Send the closure email once, on successful closure.",
                "acceptance_criteria": [
                    "An email is sent to the registered address on successful closure",
                    "The email states the closure date and that reopening needs support",
                    "No email is sent when closure does not complete",
                ],
            },
            {
                "summary": "Enforce closed-account restrictions",
                "user_story": (
                    "As a compliance officer, I would like closed accounts blocked from logging "
                    "in, depositing and receiving marketing, so that closure is meaningful."
                ),
                "details": (
                    "Apply the closed state across login, deposits and marketing preferences."
                ),
                "acceptance_criteria": [
                    "A closed account cannot log in and is told the account is closed",
                    "A closed account cannot deposit through any payment method",
                    "A closed account receives no marketing email or push",
                ],
            },
        ],
        "open_questions": [
            "Is closure immediate, or is there a cooling-off period during which the player can "
            "reverse it?",
            "Does the Ontario deadline require closure to be available in French as well?",
            "Should a pending withdrawal block closure, or complete after it?",
        ],
    },
    "DEMO-7": {
        "stories": [
            {
                "summary": "Emit a first_time_deposit event on the first successful deposit",
                "user_story": (
                    "As a marketing specialist, I would like a first_time_deposit event when a "
                    "player funds their account for the first time, so that I can measure "
                    "registration-to-deposit conversion."
                ),
                "details": (
                    "On a successful web deposit, determine whether it is the player's first. "
                    "If so, emit first_time_deposit to the analytics platform."
                ),
                "acceptance_criteria": [
                    "The event fires exactly once, on a player's first successful deposit",
                    "No event fires on a second or later deposit",
                    "No event fires on a failed or abandoned deposit",
                ],
            },
            {
                "summary": "Include amount, currency and payment type on the event",
                "user_story": (
                    "As a marketing specialist, I would like deposit value on the event, so "
                    "that I can compare channels by revenue rather than volume."
                ),
                "details": (
                    "Attach the deposit amount, and the currency and payment type where the "
                    "payment record provides them."
                ),
                "acceptance_criteria": [
                    "The event carries the deposit amount for every first deposit",
                    "Currency is included when the payment record provides it",
                    "Payment type is included when available, and omitted otherwise",
                ],
            },
            {
                "summary": "Verify recorded analytics values against payment records",
                "user_story": (
                    "As a marketing specialist, I would like the recorded values checked "
                    "against payments, so that I can trust the reporting."
                ),
                "details": (
                    "Reconcile a sample of first deposits between the analytics platform and "
                    "the payment records, and record the result."
                ),
                "acceptance_criteria": [
                    "A sample of first deposits reconciles on amount and currency",
                    "Any mismatch is raised before the change is considered done",
                ],
            },
        ],
        "open_questions": [
            "Is 'first time' per player for all time, or reset if an account is closed and "
            "reopened?",
            "Should a deposit that is later reversed or charged back retract the event?",
            "Which analytics platform is authoritative if the app and web disagree?",
        ],
    },
}


def demo_keys() -> list[str]:
    return list(DEMO_ISSUES)


def get_demo_issue(key: str) -> dict[str, Any] | None:
    return DEMO_ISSUES.get(key.strip().upper())


def get_demo_stories(key: str) -> dict[str, Any] | None:
    return DEMO_STORIES.get(key.strip().upper())
