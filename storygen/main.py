"""Jira AI Story Generator — command line entry point.

    python -m storygen.main test-connection
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

import anthropic

from .config import Config, ConfigError
from .demo_data import DEMO_ISSUES, get_demo_issue, get_demo_stories, get_demo_test_cases
from .generate import SYSTEM, build_prompt, generate_stories
from .jira import Jira, JiraError, looks_like_key
from .publish import JiraWriter, NotConfirmed, render_case, render_plain
from .testcases import generate_test_cases, render_csv, render_markdown

SUMMARY_FIELDS = ["summary", "status", "issuetype", "updated"]


def _jira(config: Config) -> Jira:
    """A read-only client for whichever deployment the site is."""
    return Jira(
        config.jira_url, config.jira_email, config.jira_token, deployment=config.deployment
    )


def _line(issue: dict) -> str:
    fields = issue.get("fields", {})
    issue_type = (fields.get("issuetype") or {}).get("name", "?")
    status = (fields.get("status") or {}).get("name", "?")
    return f"  {issue['key']:<12} {issue_type:<16} {status:<14} {fields.get('summary', '')}"


def test_connection(config: Config) -> int:
    config.check_jira()
    jira = Jira(
        config.jira_url, config.jira_email, config.jira_token, deployment=config.deployment
    )
    try:
        me = jira.myself()
    finally:
        jira.close()

    kind = "Cloud" if config.deployment == "cloud" else "Data Center / Server"
    print(f"Connected to {config.jira_url}  ({kind}, REST v{jira.api})")
    print(f"  as: {me.get('displayName')} <{me.get('emailAddress') or 'email hidden'}>")
    # Cloud identifies users by accountId; Data Center by username.
    print(f"  user: {me.get('accountId') or me.get('name') or me.get('key') or '?'}")
    return 0


def find(config: Config, query: str, demo: bool = False) -> int:
    if demo:
        matches = [
            issue for issue in DEMO_ISSUES.values()
            if query.strip().lower() in (issue["summary"] + issue["description"]).lower()
            or query.strip().upper() == issue["key"]
        ]
        print(f"[demo] {len(matches)} sample match(es) for {query!r}:")
        for issue in matches or DEMO_ISSUES.values():
            if not matches:
                print("  (no match — here is the full sample set)")
                matches = True
            print(f"  {issue['key']:<12} {issue['type']:<16} {issue['status']:<14} {issue['summary']}")
        return 0

    config.check_jira()
    jira = _jira(config)
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


def show(config: Config, key: str, demo: bool = False) -> int:
    if demo:
        issue = get_demo_issue(key)
        if issue is None:
            print(f"No sample issue {key.upper()}. Try: {', '.join(DEMO_ISSUES)}")
            return 1
        print("[demo] sample data — not from Jira\n")
    else:
        config.check_jira()
        jira = _jira(config)
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


def generate(config: Config, key: str, save: str | None, demo: bool = False) -> int:
    if demo:
        context = get_demo_issue(key)
        result = get_demo_stories(key)
        if context is None or result is None:
            print(f"No sample issue {key.upper()}. Try: {', '.join(DEMO_ISSUES)}")
            return 1
        print("[demo] sample issue and sample suggestions — no Jira, no AI call\n")
    else:
        config.check_jira()
        config.check_ai()

        jira = _jira(config)
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


def prompt(config: Config, key: str, save: str | None, demo: bool = False) -> int:
    """Print the exact prompt to paste into any AI chat.

    No API key needed — this is the escape hatch when API access isn't
    available: the tool still does the Jira reading and the prompt writing,
    and you paste the result into whichever assistant you already have.
    """
    if demo:
        context = get_demo_issue(key)
        if context is None:
            print(f"No sample issue {key.upper()}. Try: {', '.join(DEMO_ISSUES)}")
            return 1
    else:
        config.check_jira()
        jira = _jira(config)
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


def _confirm(question: str, reader: Any = input) -> bool:
    """Anything but a typed 'yes' is a no. A closed stdin is also a no."""
    try:
        answer = reader(question)
    except EOFError:
        return False
    return answer.strip().lower() == "yes"


def _preview_write(config: Config, context: dict, result: dict, target: str) -> str:
    """Print exactly what would be written, and return the comment body."""
    key = context["key"]
    cases = result.get("test_cases") or []
    body = render_plain(result, key)

    print("\n" + "=" * 70)
    print("PERMISSION NEEDED — this will change Jira")
    print("=" * 70)
    print(f"Site:   {config.jira_url}")
    print(f"Issue:  {key}")
    if target == "comment":
        print(f"Action: post ONE comment with all {len(cases)} test cases")
        print("Undo:   delete that comment in Jira")
    else:
        project = context.get("project") or key.split("-")[0]
        print(f"Action: create {len(cases)} sub-tasks under {key} in project {project}")
        for case in cases:
            print(f"          {case.get('id', ''):<7} {case.get('title', '')}")
        print("Undo:   none from this tool — deleting issues needs a Jira permission")
    print("=" * 70)
    return body


def _write_to_jira(config: Config, context: dict, result: dict, target: str) -> int:
    key = context["key"]
    project = context.get("project") or key.split("-")[0]
    writer = JiraWriter(
        config.jira_url, config.jira_email, config.jira_token, deployment=config.deployment
    )
    created: list[str] = []
    try:
        if target == "comment":
            comment = writer.add_comment(key, render_plain(result, key), confirmed=True)
            print(f"\nPosted comment {comment.get('id', '')} on {key}")
        else:
            for case in result.get("test_cases") or []:
                summary = f"{case.get('id', '')} {case.get('title', '')}".strip()
                issue = writer.create_subtask(
                    parent_key=key,
                    project_key=project,
                    summary=summary,
                    description=render_case(case),
                    confirmed=True,
                )
                created.append(issue.get("key", "?"))
                print(f"  created {issue.get('key', '?')}  {summary}")
            print(f"\nCreated {len(created)} sub-tasks under {key}")
    except JiraError:
        if created:
            print(
                f"\nStopped after creating {len(created)}: {', '.join(created)}. "
                "Those remain in Jira.",
                file=sys.stderr,
            )
        raise
    finally:
        writer.close()

    print(f"{config.jira_url}/browse/{key}")
    return 0


def testcases(
    config: Config,
    key: str,
    save: str | None = None,
    csv_path: str | None = None,
    post: bool = False,
    target: str = "comment",
    assume_yes: bool = False,
    demo: bool = False,
) -> int:
    """Generate manual test cases for an issue. Writes to Jira only if asked."""
    if demo:
        context = get_demo_issue(key)
        result = get_demo_test_cases(key)
        if context is None or result is None:
            print(f"No sample issue {key.upper()}. Try: {', '.join(DEMO_ISSUES)}")
            return 1
        if post:
            print("Error: --post needs a real Jira. Drop --demo.", file=sys.stderr)
            return 1
        print("[demo] sample issue and sample test cases — no Jira, no AI call\n")
    else:
        config.check_jira()
        config.check_ai()

        jira = _jira(config)
        try:
            context = jira.get_context(key.strip().upper())
        finally:
            jira.close()

        if not context["description"]:
            print(f"Warning: {context['key']} has no description — test cases will be thin.\n")

        print(f"Reading {context['key']} and asking {config.model}… (this takes a few seconds)\n")
        result = generate_test_cases(context, config.anthropic_key, config.model)

    print(render_markdown(context["key"], result, context.get("url", "")))

    if save:
        with open(save, "w", encoding="utf-8") as handle:
            handle.write(render_markdown(context["key"], result, context.get("url", "")) + "\n")
        print(f"\nSaved to {save}")
    if csv_path:
        with open(csv_path, "w", encoding="utf-8", newline="") as handle:
            handle.write(render_csv(result))
        print(f"Saved to {csv_path} (one row per step — imports into Xray, Zephyr, TestRail)")

    if not post:
        print("\nNothing was written to Jira. Add --post to publish (it asks first).")
        return 0

    _preview_write(config, context, result, target)
    if assume_yes:
        print("--yes was given, so not asking. Writing now.")
    elif not _confirm("Write this to Jira? Type 'yes' to continue: "):
        print("Not written. Jira is unchanged.")
        return 0

    return _write_to_jira(config, context, result, target)


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

    tc_cmd = commands.add_parser(
        "testcases", help="Write manual test cases for an issue (asks before writing to Jira)"
    )
    tc_cmd.add_argument("key", help="Issue key, e.g. DFE-9067")
    tc_cmd.add_argument("--save", metavar="FILE", help="Also write the test cases to a Markdown file")
    tc_cmd.add_argument(
        "--csv", metavar="FILE", dest="csv_path", help="Also write a CSV for Xray/Zephyr/TestRail"
    )
    tc_cmd.add_argument(
        "--post",
        action="store_true",
        help="Offer to publish to Jira. You are shown what will be written and asked first.",
    )
    tc_cmd.add_argument(
        "--as",
        dest="target",
        choices=("comment", "subtasks"),
        default="comment",
        help="Where to publish: one comment (default, reversible) or one sub-task per case",
    )
    tc_cmd.add_argument(
        "--yes",
        action="store_true",
        dest="assume_yes",
        help="Skip the confirmation prompt. Only meaningful with --post; for scripted runs.",
    )

    for sub in (find_cmd, show_cmd, gen_cmd, prompt_cmd, tc_cmd):
        sub.add_argument(
            "--demo",
            action="store_true",
            help="Use built-in sample data — no Jira and no API key needed",
        )

    args = parser.parse_args(argv)
    config = Config.load()

    try:
        if args.command == "test-connection":
            return test_connection(config)
        if args.command == "find":
            return find(config, args.query, args.demo)
        if args.command == "show":
            return show(config, args.key, args.demo)
        if args.command == "generate":
            return generate(config, args.key, args.save, args.demo)
        if args.command == "prompt":
            return prompt(config, args.key, args.save, args.demo)
        if args.command == "testcases":
            return testcases(
                config,
                args.key,
                args.save,
                args.csv_path,
                args.post,
                args.target,
                args.assume_yes,
                args.demo,
            )
    except NotConfirmed as exc:
        # A write path was reached without consent — a bug, not a user error.
        print(f"Error: {exc}", file=sys.stderr)
        return 1
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
