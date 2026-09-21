"""Configuration from environment variables (or .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


class ConfigError(RuntimeError):
    pass


@dataclass
class Config:
    jira_url: str
    jira_email: str
    jira_token: str
    anthropic_key: str
    model: str = "claude-opus-5"

    @classmethod
    def load(cls) -> "Config":
        return cls(
            jira_url=os.getenv("JIRA_BASE_URL", "").rstrip("/"),
            jira_email=os.getenv("JIRA_EMAIL", ""),
            jira_token=os.getenv("JIRA_API_TOKEN", ""),
            anthropic_key=os.getenv("ANTHROPIC_API_KEY", ""),
            model=os.getenv("CLAUDE_MODEL", "claude-opus-5"),
        )

    def check_jira(self) -> None:
        """Raise if the Jira settings are missing. AI settings aren't needed yet."""
        missing = [
            name
            for name, value in (
                ("JIRA_BASE_URL", self.jira_url),
                ("JIRA_EMAIL", self.jira_email),
                ("JIRA_API_TOKEN", self.jira_token),
            )
            if not value
        ]
        if missing:
            raise ConfigError(
                "Missing: " + ", ".join(missing) + "\nCopy .env.example to .env and fill it in."
            )

    def check_ai(self) -> None:
        """Only needed by the generate command."""
        if not self.anthropic_key:
            raise ConfigError(
                "Missing: ANTHROPIC_API_KEY\n"
                "Get one at https://console.anthropic.com/settings/keys, then set it "
                "the same way as the Jira settings."
            )
