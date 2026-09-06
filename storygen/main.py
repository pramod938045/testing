"""Jira AI Story Generator — command line entry point.

    python -m storygen.main test-connection
"""

from __future__ import annotations

import argparse
import sys

from .config import Config, ConfigError
from .jira import Jira, JiraError, looks_like_key

SUMMARY_FIELDS = ["summary", "status", "issuetype", "updated"]


def _line(issue: dict) -> str:
    fields = issue.get("fields", {})
    issue_type = (fields.get("issuetype") or {}).get("name", "?")
    status = (fields.get("status") or {}).get("name", "?")
    return f"  {issue['key']:<12} {issue_type:<16} {status:<14} {fields.get('summary', '')}"


def test_connection(config: Config) -> int:
    config.check_jira()
    jira = Jira(config.jira_url, config.jira_email, config.jira_token)
    try:
        me = jira.myself()
    finally:
        jira.close()

    print(f"Connected to {config.jira_url}")
    print(f"  as: {me.get('displayName')} <{me.get('emailAddress') or 'email hidden'}>")
    print(f"  account id: {me.get('accountId')}")
    return 0


def find(config: Config, query: str) -> int:
    config.check_jira()
    jira = Jira(config.jira_url, config.jira_email, config.jira_token)
    try:
        if looks_like_key(query):
            issue = jira.get_issue(query.strip().upper(), SUMMARY_FIELDS)
            print("Found:")
            print(_line(issue))
            return 0

        issues, type_filtered = jira.find_parents(query)
        if not type_filtered:
            print("Note: this site has no 'Epic'/'Change Request' types — searched all types.\n")
        if not issues:
            print(f"No Epics or Change Requests match {query!r}.")
            return 0

        print(f"{len(issues)} match{'' if len(issues) == 1 else 'es'} for {query!r}:")
        for issue in issues:
            print(_line(issue))
        return 0
    finally:
        jira.close()


def show(config: Config, key: str) -> int:
    config.check_jira()
    jira = Jira(config.jira_url, config.jira_email, config.jira_token)
    try:
        issue = jira.get_context(key.strip().upper())
    finally:
        jira.close()

    print(f"{issue['key']}  [{issue['type']} / {issue['status']}]  {issue['url']}")
    print(f"Summary: {issue['summary']}")
    meta = [f"project {issue['project']}"]
    if issue["priority"]:
        meta.append(f"priority {issue['priority']}")
    if issue["labels"]:
        meta.append("labels " + ", ".join(issue["labels"]))
    print("  (" + "; ".join(meta) + ")")

    print("\n--- Description ---")
    print(issue["description"] or "(empty)")

    if issue["links"]:
        print(f"\n--- Linked issues ({len(issue['links'])}) ---")
        for link in issue["links"]:
            print(f"  {link['relation']:<18} {link['key']:<12} [{link['status']}] {link['summary']}")

    if issue["subtasks"]:
        print(f"\n--- Existing subtasks ({len(issue['subtasks'])}) ---")
        for sub in issue["subtasks"]:
            print(f"  {sub['key']:<12} {sub['summary']}")

    if not issue["description"]:
        print("\nNote: no description — the AI will have only the summary to work from.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="storygen", description="Jira AI Story Generator")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("test-connection", help="Check the Jira credentials work")
    find_cmd = commands.add_parser("find", help="Find an Epic or Change Request")
    find_cmd.add_argument("query", help="An issue key (ABC-42) or text to search for")
    show_cmd = commands.add_parser("show", help="Read one issue: description and linked issues")
    show_cmd.add_argument("key", help="Issue key, e.g. DFE-9067")

    args = parser.parse_args(argv)
    config = Config.load()

    try:
        if args.command == "test-connection":
            return test_connection(config)
        if args.command == "find":
            return find(config, args.query)
        if args.command == "show":
            return show(config, args.key)
    except (ConfigError, JiraError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
