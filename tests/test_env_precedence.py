"""The .env file must beat leftover shell variables.

python-dotenv defaults to override=False, so a stale `set JIRA_BASE_URL=...`
silently wins over the file `app.setup` just wrote: settings look saved and do
nothing. Moving a .env from Cloud to Data Center hits this twice — the URL and
token are overridden, and the deleted JIRA_EMAIL comes back from the shell.
"""

import os

from app.config import load_env_file

CLOUD = "https://scientificgames.atlassian.net"
SERVER = "https://jira.scigames.at"


def write_env(tmp_path, **values):
    path = tmp_path / ".env"
    path.write_text("\n".join(f"{k}={v}" for k, v in values.items()) + "\n")
    return str(path)


def test_the_file_beats_a_shell_variable(monkeypatch, tmp_path):
    monkeypatch.setenv("JIRA_BASE_URL", CLOUD)
    path = write_env(tmp_path, JIRA_BASE_URL=SERVER, JIRA_API_TOKEN="pat")

    report = load_env_file(path)

    assert os.environ["JIRA_BASE_URL"] == SERVER
    assert report["shadowed"] == ["JIRA_BASE_URL"]


def test_a_key_the_file_omits_is_cleared(monkeypatch, tmp_path):
    """Data Center has no email; a stale one must not come back."""
    monkeypatch.setenv("JIRA_EMAIL", "pramod.rayanagoudra@scientificgames.com")
    monkeypatch.setenv("JIRA_BASE_URL", CLOUD)
    path = write_env(tmp_path, JIRA_BASE_URL=SERVER, JIRA_API_TOKEN="pat")

    report = load_env_file(path)

    assert "JIRA_EMAIL" not in os.environ
    assert report["cleared"] == ["JIRA_EMAIL"]


def test_the_cloud_token_is_replaced_not_kept(monkeypatch, tmp_path):
    monkeypatch.setenv("JIRA_API_TOKEN", "ATATT3xFfGF0Wkph8g" + "x" * 174)
    path = write_env(tmp_path, JIRA_BASE_URL=SERVER, JIRA_API_TOKEN="dc-pat")

    load_env_file(path)

    assert os.environ["JIRA_API_TOKEN"] == "dc-pat"


def test_jira_vars_are_only_cleared_when_the_file_configures_jira(monkeypatch, tmp_path):
    """A .env holding only an API key must not wipe a shell Jira setup."""
    monkeypatch.setenv("JIRA_BASE_URL", CLOUD)
    monkeypatch.setenv("JIRA_EMAIL", "me@example.com")
    path = write_env(tmp_path, ANTHROPIC_API_KEY="sk-ant-test")

    report = load_env_file(path)

    assert os.environ["JIRA_BASE_URL"] == CLOUD
    assert os.environ["JIRA_EMAIL"] == "me@example.com"
    assert report["cleared"] == []


def test_non_jira_variables_are_never_cleared(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-shell")
    path = write_env(tmp_path, JIRA_BASE_URL=SERVER, JIRA_API_TOKEN="pat")

    load_env_file(path)

    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-from-shell"


def test_an_unchanged_value_is_not_reported_as_shadowed(monkeypatch, tmp_path):
    monkeypatch.setenv("JIRA_BASE_URL", SERVER)
    path = write_env(tmp_path, JIRA_BASE_URL=SERVER, JIRA_API_TOKEN="pat")

    assert load_env_file(path)["shadowed"] == []


def test_a_missing_file_changes_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("JIRA_BASE_URL", CLOUD)

    report = load_env_file("")

    assert os.environ["JIRA_BASE_URL"] == CLOUD
    assert report == {"path": "", "names": [], "shadowed": [], "cleared": []}


def test_the_report_carries_no_values(monkeypatch, tmp_path):
    monkeypatch.setenv("JIRA_API_TOKEN", "old-secret")
    path = write_env(tmp_path, JIRA_BASE_URL=SERVER, JIRA_API_TOKEN="new-secret")

    report = load_env_file(path)

    flat = repr(report)
    assert "old-secret" not in flat and "new-secret" not in flat
