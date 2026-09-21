"""Create or update the .env file: `python -m app.setup`.

Editing .env by hand on Windows goes wrong in ways that are hard to see —
Notepad saves it as ".env.txt", a stray space or quote ends up in the value,
or the file lands in the wrong folder. This asks four questions and writes
the file correctly, so settings survive closing the terminal and no `set`
commands are needed.
"""

from __future__ import annotations

from pathlib import Path

from .jira_client import detect_deployment

CLEAR = "-"

FIELDS = [
    ("JIRA_BASE_URL", "Your Jira site URL", "https://jira.scigames.at", False),
    ("JIRA_EMAIL", "The Atlassian account email", "", False),
    ("JIRA_API_TOKEN", "Jira API token", "", True),
    ("ANTHROPIC_API_KEY", "Anthropic API key — leave blank if you don't have one yet", "", True),
]

# What each Jira asks for, once the URL says which one it is.
LABELS = {
    "cloud": {
        "JIRA_EMAIL": "The Atlassian account email (Cloud signs in with email + API token)",
        "JIRA_API_TOKEN": "Cloud API token, from id.atlassian.com -> Security -> API tokens",
    },
    "server": {
        "JIRA_EMAIL": (
            f"Email — Data Center does NOT use one. Type {CLEAR} to clear it, "
            "or press Enter to leave it as-is"
        ),
        "JIRA_API_TOKEN": (
            "Personal Access Token, from your Jira -> avatar -> Profile -> "
            "Personal Access Tokens. A Cloud API token will not work here"
        ),
    },
}


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
    deployment = detect_deployment(existing.get("JIRA_BASE_URL", ""))

    for name, label, example, secret in FIELDS:
        current = existing.get(name, "")
        shown = mask(current) if secret else (current or "(not set)")
        hint = f"  e.g. {example}" if example and not current else ""
        print(f"{LABELS.get(deployment, {}).get(name, label)}{hint}")
        try:
            entered = input(f"  {name} [{shown}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled — nothing was written.")
            return 1

        # Strip quotes people paste along with the value.
        entered = entered.strip('"').strip("'").strip()
        values[name] = "" if entered == CLEAR else (entered or current)

        if name == "JIRA_BASE_URL" and values[name]:
            values[name] = values[name].rstrip("/")
            deployment = detect_deployment(values[name])
            print(f"  -> {deployment}: REST API v{'3' if deployment == 'cloud' else '2'}, "
                  f"{'Basic email + API token' if deployment == 'cloud' else 'Bearer personal access token'}")
        print()

    required = ["JIRA_BASE_URL", "JIRA_API_TOKEN"]
    if deployment == "cloud":
        # Only Cloud signs in as an email address; a Data Center PAT stands alone.
        required.append("JIRA_EMAIL")
    missing = [name for name in required if not values.get(name)]
    if missing:
        print(f"{deployment} Jira needs: " + ", ".join(missing) + ". Nothing was written.")
        return 1

    if deployment == "server" and values.get("JIRA_EMAIL"):
        print("Note: JIRA_EMAIL is ignored on Data Center — the token authenticates alone.\n")

    lines = [
        "# Written by `python -m app.setup`. Keep this file private.",
        *(f"{name}={values[name]}" for name, _, _, _ in FIELDS if values.get(name)),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Saved {path}  ({deployment} Jira, REST v{'3' if deployment == 'cloud' else '2'})")
    print("These settings now load automatically — no more `set` commands.\n")

    if not values.get("ANTHROPIC_API_KEY"):
        print("No Anthropic key yet, so the chatbot cannot answer questions.")
        print("Ticket lookup and the storygen commands work without it.")
        print("Add the key later by running this again.\n")

    print("Next:  python -m app.jira_test ISSUE-KEY      (checks the Jira connection)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
