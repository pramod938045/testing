"""Configuration loaded from environment variables (or a local .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


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

    def validate(self) -> None:
        missing = [
            name
            for name, value in (
                ("JIRA_BASE_URL", self.jira_base_url),
                ("JIRA_EMAIL", self.jira_email),
                ("JIRA_API_TOKEN", self.jira_api_token),
            )
            if not value
        ]
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
