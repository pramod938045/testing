"""Jira AI Story Generator — command line entry point.

    python -m storygen.main test-connection
"""

from __future__ import annotations

import argparse
import sys

from .config import Config, ConfigError
from .jira import Jira, JiraError


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="storygen", description="Jira AI Story Generator")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("test-connection", help="Check the Jira credentials work")

    args = parser.parse_args(argv)
    config = Config.load()

    try:
        if args.command == "test-connection":
            return test_connection(config)
    except (ConfigError, JiraError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
