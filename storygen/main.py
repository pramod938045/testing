"""Jira AI Story Generator — command line entry point.

    python -m storygen.main test-connection
"""

from __future__ import annotations

import argparse
import sys

import anthropic

from .config import Config, ConfigError
from .generate import SYSTEM, build_prompt, generate_stories
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


def _render(key: str, result: dict) -> str:
    """The review text: what the AI suggests, for a human to judge."""
    lines = [f"# Suggested Stories for {key}", ""]
    stories = result.get("stories") or []

    for index, story in enumerate(stories, start=1):
        lines.append(f"## Story {index}: {story.get('summary', '')}")
        lines.append("")
        if story.get("user_story"):
            lines.append(story["user_story"])
            lines.append("")
        if story.get("details"):
            lines.append(story["details"])
            lines.append("")
        criteria = story.get("acceptance_criteria") or []
        if criteria:
            lines.append("Acceptance criteria:")
            lines += [f"  - {item}" for item in criteria]
            lines.append("")

    questions = result.get("open_questions") or []
    if questions:
        lines.append("## Open questions (the issue does not answer these)")
        lines += [f"  - {item}" for item in questions]
        lines.append("")

    lines.append(f"({len(stories)} stories suggested. Nothing has been created in Jira.)")
    return "\n".join(lines)


def generate(config: Config, key: str, save: str | None) -> int:
    config.check_jira()
    config.check_ai()

    jira = Jira(config.jira_url, config.jira_email, config.jira_token)
    try:
        context = jira.get_context(key.strip().upper())
    finally:
        jira.close()

    if not context["description"]:
        print(f"Warning: {context['key']} has no description — suggestions will be thin.\n")

    print(f"Reading {context['key']} and asking {config.model}… (this takes a few seconds)\n")
    result = generate_stories(context, config.anthropic_key, config.model)

    text = _render(context["key"], result)
    print(text)

    if save:
        with open(save, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
        print(f"\nSaved to {save}")
    return 0


def prompt(config: Config, key: str, save: str | None) -> int:
    """Print the exact prompt to paste into any AI chat.

    No API key needed — this is the escape hatch when API access isn't
    available: the tool still does the Jira reading and the prompt writing,
    and you paste the result into whichever assistant you already have.
    """
    config.check_jira()
    jira = Jira(config.jira_url, config.jira_email, config.jira_token)
    try:
        context = jira.get_context(key.strip().upper())
    finally:
        jira.close()

    text = f"{SYSTEM}\n\n---\n\n{build_prompt(context)}"

    if save:
        with open(save, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
        print(f"Prompt for {context['key']} written to {save}")
        print("Open that file, copy everything, and paste it into your AI chat.")
    else:
        print("=" * 70)
        print("Copy everything between the lines into your AI chat:")
        print("=" * 70)
        print(text)
        print("=" * 70)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="storygen", description="Jira AI Story Generator")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("test-connection", help="Check the Jira credentials work")
    find_cmd = commands.add_parser("find", help="Find an Epic or Change Request")
    find_cmd.add_argument("query", help="An issue key (ABC-42) or text to search for")
    show_cmd = commands.add_parser("show", help="Read one issue: description and linked issues")
    show_cmd.add_argument("key", help="Issue key, e.g. DFE-9067")
    gen_cmd = commands.add_parser("generate", help="Suggest Stories for an issue (read-only)")
    gen_cmd.add_argument("key", help="Issue key, e.g. DFE-9067")
    gen_cmd.add_argument("--save", metavar="FILE", help="Also write the suggestions to a file")
    prompt_cmd = commands.add_parser(
        "prompt", help="Print a ready-to-paste AI prompt (no API key needed)"
    )
    prompt_cmd.add_argument("key", help="Issue key, e.g. DFE-9067")
    prompt_cmd.add_argument("--save", metavar="FILE", help="Write the prompt to a file instead")

    args = parser.parse_args(argv)
    config = Config.load()

    try:
        if args.command == "test-connection":
            return test_connection(config)
        if args.command == "find":
            return find(config, args.query)
        if args.command == "show":
            return show(config, args.key)
        if args.command == "generate":
            return generate(config, args.key, args.save)
        if args.command == "prompt":
            return prompt(config, args.key, args.save)
    except (ConfigError, JiraError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except anthropic.AuthenticationError:
        print("Error: the AI rejected your key. Check ANTHROPIC_API_KEY.", file=sys.stderr)
        return 1
    except anthropic.RateLimitError:
        print("Error: the AI is rate limiting. Wait a moment and try again.", file=sys.stderr)
        return 1
    except anthropic.APIConnectionError:
        print("Error: could not reach the AI service. Check your network.", file=sys.stderr)
        return 1
    except (anthropic.APIStatusError, RuntimeError, ValueError) as exc:
        print(f"Error from the AI: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
