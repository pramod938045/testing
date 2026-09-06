"""Create or update the .env file: `python -m app.setup`.

Editing .env by hand on Windows goes wrong in ways that are hard to see —
Notepad saves it as ".env.txt", a stray space or quote ends up in the value,
or the file lands in the wrong folder. This asks four questions and writes
the file correctly, so settings survive closing the terminal and no `set`
commands are needed.
"""

from __future__ import annotations

import sys
from pathlib import Path

FIELDS = [
    ("JIRA_BASE_URL", "Your Jira site URL", "https://scientificgames.atlassian.net", False),
    ("JIRA_EMAIL", "The Atlassian account email", "", False),
    ("JIRA_API_TOKEN", "Jira API token (id.atlassian.com -> Security -> API tokens)", "", True),
    ("ANTHROPIC_API_KEY", "Anthropic API key — leave blank if you don't have one yet", "", True),
]


def read_existing(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            name, _, value = line.partition("=")
            values[name.strip()] = value.strip()
    return values


def mask(value: str) -> str:
    """Show enough to recognise a value, never enough to use it."""
    if not value:
        return "(not set)"
    return f"(set, {len(value)} characters, ends …{value[-4:]})" if len(value) > 8 else "(set)"


def main() -> int:
    path = Path.cwd() / ".env"
    existing = read_existing(path)

    print(f"\nWriting settings to: {path}")
    print("Press Enter to keep the current value shown in brackets.\n")

    values: dict[str, str] = {}
    for name, label, example, secret in FIELDS:
        current = existing.get(name, "")
        shown = mask(current) if secret else (current or "(not set)")
        hint = f"  e.g. {example}" if example and not current else ""
        print(f"{label}{hint}")
        try:
            entered = input(f"  {name} [{shown}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled — nothing was written.")
            return 1

        # Strip quotes people paste along with the value.
        entered = entered.strip('"').strip("'").strip()
        values[name] = entered or current
        print()

    missing = [n for n in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN") if not values.get(n)]
    if missing:
        print("Jira needs all three of: " + ", ".join(missing) + ". Nothing was written.")
        return 1

    lines = [
        "# Written by `python -m app.setup`. Keep this file private.",
        *(f"{name}={values[name]}" for name, _, _, _ in FIELDS if values.get(name)),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Saved {path}")
    print("These settings now load automatically — no more `set` commands.\n")

    if not values.get("ANTHROPIC_API_KEY"):
        print("No Anthropic key yet, so the chatbot cannot answer questions.")
        print("Ticket lookup and the storygen commands work without it.")
        print("Add the key later by running this again.\n")

    print("Next:  python -m app.check")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
