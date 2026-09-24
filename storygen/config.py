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
    jira_deployment: str = "auto"

    @classmethod
    def load(cls) -> "Config":
        return cls(
            jira_url=os.getenv("JIRA_BASE_URL", "").rstrip("/"),
            jira_email=os.getenv("JIRA_EMAIL", ""),
            jira_token=os.getenv("JIRA_API_TOKEN", ""),
            anthropic_key=os.getenv("ANTHROPIC_API_KEY", ""),
            model=os.getenv("CLAUDE_MODEL", "claude-opus-5"),
            jira_deployment=os.getenv("JIRA_DEPLOYMENT", "auto").lower(),
        )

    @property
    def deployment(self) -> str:
        """cloud or server, resolved from JIRA_DEPLOYMENT or the site URL."""
        if self.jira_deployment in ("cloud", "server"):
            return self.jira_deployment
        from .jira import detect_deployment

        return detect_deployment(self.jira_url)

    def check_jira(self) -> None:
        """Raise if the Jira settings are missing. AI settings aren't needed yet."""
        required = [("JIRA_BASE_URL", self.jira_url), ("JIRA_API_TOKEN", self.jira_token)]
        # Data Center authenticates with the token alone; Cloud needs the email.
        if self.deployment == "cloud":
            required.insert(1, ("JIRA_EMAIL", self.jira_email))

        missing = [name for name, value in required if not value]
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
