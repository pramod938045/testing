"""Configuration loaded from environment variables (or a local .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import dotenv_values, find_dotenv, load_dotenv

# The .env file owns the Jira block as a whole. A key it deliberately leaves
# out — JIRA_EMAIL on Data Center, say — must not be resurrected from a stale
# shell variable, so those are cleared alongside the ones it does set.
JIRA_VARS = (
    "JIRA_BASE_URL",
    "JIRA_EMAIL",
    "JIRA_API_TOKEN",
    "JIRA_DEPLOYMENT",
    "JIRA_DEFAULT_PROJECT",
    "JIRA_TIMEOUT_SECONDS",
)


def load_env_file(path: str | None = None) -> dict:
    """Load .env so that the file beats variables already in the shell.

    python-dotenv defaults to `override=False`, which means a leftover
    `set JIRA_BASE_URL=...` silently wins over the file that `app.setup`
    just wrote — the settings look saved and have no effect. For a local
    tool the file the user edited is what they meant, so it wins here.

    Returns what happened, for diagnostics. Values are never included.
    """
    if path is None:
        path = find_dotenv(usecwd=True) or find_dotenv()
    file_values = {
        name: value for name, value in (dotenv_values(path) if path else {}).items()
        if value is not None
    }

    # Names the shell had set to something else; the file now wins.
    shadowed = sorted(
        name for name, value in file_values.items()
        if os.getenv(name) is not None and os.getenv(name) != value
    )
    if path:
        load_dotenv(path, override=True)

    cleared = []
    if "JIRA_BASE_URL" in file_values:
        for name in JIRA_VARS:
            if name not in file_values and name in os.environ:
                del os.environ[name]
                cleared.append(name)

    return {"path": path or "", "names": sorted(file_values), "shadowed": shadowed, "cleared": cleared}


ENV_FILE = load_env_file()
DOTENV_PATH = ENV_FILE["path"]


def source_of(name: str) -> str:
    """Where a setting's value came from — for messages, never the value."""
    if name in ENV_FILE["names"]:
        return "the .env file" + (" (overriding the shell)" if name in ENV_FILE["shadowed"] else "")
    if name in ENV_FILE["cleared"]:
        return "cleared — not in .env"
    return "the shell environment" if os.getenv(name) else "not set"


def api_key_report() -> dict:
    """Safe facts about ANTHROPIC_API_KEY — never the key itself."""
    in_file = ((dotenv_values(DOTENV_PATH) if DOTENV_PATH else {}).get("ANTHROPIC_API_KEY") or "").strip()
    live = os.getenv("ANTHROPIC_API_KEY", "").strip()

    return {
        "dotenv_file": DOTENV_PATH or "(none found)",
        "key_in_dotenv": bool(in_file),
        "key_in_use": bool(live),
        "key_length": len(live),
        "prefix_looks_right": live.startswith("sk-ant-"),
        "looks_like_placeholder": "your-key" in live or "paste" in live.lower(),
        "shell_overrides_dotenv": False,  # the file now wins; kept for the health payload
        "source": source_of("ANTHROPIC_API_KEY"),
    }


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


class ConfigError(RuntimeError):
    """Raised when required configuration is missing."""


@dataclass
class Settings:
    # --- Jira Cloud ---
    jira_base_url: str = field(default_factory=lambda: os.getenv("JIRA_BASE_URL", "").rstrip("/"))
    jira_email: str = field(default_factory=lambda: os.getenv("JIRA_EMAIL", ""))
    jira_api_token: str = field(default_factory=lambda: os.getenv("JIRA_API_TOKEN", ""))
    jira_default_project: str = field(default_factory=lambda: os.getenv("JIRA_DEFAULT_PROJECT", ""))
    jira_timeout_seconds: float = field(default_factory=lambda: float(os.getenv("JIRA_TIMEOUT_SECONDS", "30")))
    # cloud | server | auto — "auto" reads it from the site URL.
    jira_deployment: str = field(default_factory=lambda: os.getenv("JIRA_DEPLOYMENT", "auto").lower())

    # --- Claude ---
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    model: str = field(default_factory=lambda: os.getenv("CLAUDE_MODEL", "claude-opus-5"))
    effort: str = field(default_factory=lambda: os.getenv("CLAUDE_EFFORT", "high"))
    max_tokens: int = field(default_factory=lambda: _int("CLAUDE_MAX_TOKENS", 8000))

    # --- Slack (only needed for slack_bot.py) ---
    slack_bot_token: str = field(default_factory=lambda: os.getenv("SLACK_BOT_TOKEN", ""))
    slack_app_token: str = field(default_factory=lambda: os.getenv("SLACK_APP_TOKEN", ""))

    # --- Behaviour ---
    max_tool_iterations: int = field(default_factory=lambda: _int("MAX_TOOL_ITERATIONS", 12))
    max_search_results: int = field(default_factory=lambda: _int("MAX_SEARCH_RESULTS", 50))
    history_turns: int = field(default_factory=lambda: _int("HISTORY_TURNS", 20))

    @property
    def deployment(self) -> str:
        """cloud or server, resolved from JIRA_DEPLOYMENT or the site URL."""
        if self.jira_deployment in ("cloud", "server"):
            return self.jira_deployment
        from .jira_client import detect_deployment

        return detect_deployment(self.jira_base_url)

    def validate(self) -> None:
        required = [
            ("JIRA_BASE_URL", self.jira_base_url),
            ("JIRA_API_TOKEN", self.jira_api_token),
        ]
        # Data Center authenticates with the token alone; Cloud needs the email.
        if self.deployment == "cloud":
            required.insert(1, ("JIRA_EMAIL", self.jira_email))

        missing = [name for name, value in required if not value]
        if missing:
            raise ConfigError(
                "Missing required environment variables: "
                + ", ".join(missing)
                + ". Copy .env.example to .env and fill it in."
            )
        if not self.jira_base_url.startswith(("http://", "https://")):
            raise ConfigError("JIRA_BASE_URL must start with https:// (e.g. https://acme.atlassian.net)")

    def validate_slack(self) -> None:
        """Extra checks for the Slack bot; the web app doesn't need these."""
        self.validate()
        if not self.slack_bot_token.startswith("xoxb-"):
            raise ConfigError("SLACK_BOT_TOKEN must be the bot token (starts with 'xoxb-').")
        if not self.slack_app_token.startswith("xapp-"):
            raise ConfigError(
                "SLACK_APP_TOKEN must be an app-level token (starts with 'xapp-') with the "
                "connections:write scope, so the bot can use Socket Mode."
            )


settings = Settings()
