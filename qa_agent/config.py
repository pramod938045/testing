"""TestRail settings. Jira and Claude settings come from `app.config`, which also
loads .env (with the file winning over stale shell variables)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from app.config import ConfigError


def _optional_int(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else None


@dataclass
class TestRailSettings:
    __test__ = False

    base_url: str = field(default_factory=lambda: os.getenv("TESTRAIL_BASE_URL", "").rstrip("/"))
    email: str = field(default_factory=lambda: os.getenv("TESTRAIL_EMAIL", ""))
    api_key: str = field(default_factory=lambda: os.getenv("TESTRAIL_API_KEY", ""))
    default_project_id: int | None = field(default_factory=lambda: _optional_int("TESTRAIL_DEFAULT_PROJECT_ID"))
    template_id: int | None = field(default_factory=lambda: _optional_int("TESTRAIL_TEMPLATE_ID"))
    # separated | text — which step fields new cases are written with.
    steps_format: str = field(default_factory=lambda: os.getenv("TESTRAIL_STEPS_FORMAT", "separated").lower())
    timeout_seconds: float = field(default_factory=lambda: float(os.getenv("TESTRAIL_TIMEOUT_SECONDS", "30")))

    def validate(self) -> None:
        missing = [
            name
            for name, value in (
                ("TESTRAIL_BASE_URL", self.base_url),
                ("TESTRAIL_EMAIL", self.email),
                ("TESTRAIL_API_KEY", self.api_key),
            )
            if not value
        ]
        if missing:
            raise ConfigError(
                "Missing required environment variables: " + ", ".join(missing)
                + ". See the TestRail block in .env.example."
            )
        if not self.base_url.startswith(("http://", "https://")):
            raise ConfigError("TESTRAIL_BASE_URL must start with https:// (e.g. https://acme.testrail.io)")
        if self.steps_format not in ("separated", "text"):
            raise ConfigError("TESTRAIL_STEPS_FORMAT must be 'separated' or 'text'.")
