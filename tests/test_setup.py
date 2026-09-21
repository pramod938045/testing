"""Writing .env: the questions must match the Jira the URL names.

Data Center authenticates with a bare personal access token, so `setup` must
not demand an email — and must let an existing Cloud email be cleared, which
is the exact step that blocks moving a .env from Cloud to Data Center.
"""

import app.setup as setup

SERVER = "https://jira.scigames.at"
CLOUD = "https://scientificgames.atlassian.net"


def run(monkeypatch, tmp_path, answers, existing=None):
    """Drive the prompts with canned answers; return (exit code, written .env)."""
    monkeypatch.chdir(tmp_path)
    env = tmp_path / ".env"
    if existing:
        env.write_text("\n".join(f"{k}={v}" for k, v in existing.items()) + "\n")

    replies = iter(answers)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(replies))
    code = setup.main()
    return code, setup.read_existing(env) if env.is_file() else {}


CLOUD_ENV = {
    "JIRA_BASE_URL": CLOUD,
    "JIRA_EMAIL": "pramod.rayanagoudra@scientificgames.com",
    "JIRA_API_TOKEN": "ATATT-cloud-token",
}


# --- moving a Cloud .env to Data Center ------------------------------------


def test_a_cloud_email_can_be_cleared_for_data_center(monkeypatch, tmp_path):
    code, written = run(
        monkeypatch,
        tmp_path,
        answers=[SERVER, setup.CLEAR, "pat-token", ""],
        existing=CLOUD_ENV,
    )

    assert code == 0
    assert written["JIRA_BASE_URL"] == SERVER
    assert "JIRA_EMAIL" not in written, "the Cloud email must not survive"
    assert written["JIRA_API_TOKEN"] == "pat-token"


def test_data_center_does_not_demand_an_email(monkeypatch, tmp_path):
    code, written = run(monkeypatch, tmp_path, answers=[SERVER, "", "pat-token", ""])

    assert code == 0
    assert written["JIRA_BASE_URL"] == SERVER
    assert "JIRA_EMAIL" not in written


def test_cloud_still_demands_an_email(monkeypatch, tmp_path, capsys):
    code, written = run(monkeypatch, tmp_path, answers=[CLOUD, "", "token", ""])

    assert code == 1
    assert "JIRA_EMAIL" in capsys.readouterr().out, "the failure names what is missing"
    assert written == {}, "nothing is written when a required value is missing"


def test_the_deployment_is_reported_from_the_url(monkeypatch, tmp_path, capsys):
    run(monkeypatch, tmp_path, answers=[SERVER, "", "pat-token", ""])

    out = capsys.readouterr().out
    assert "Bearer personal access token" in out
    assert "REST API v2" in out


def test_cloud_is_reported_as_basic_and_v3(monkeypatch, tmp_path, capsys):
    run(monkeypatch, tmp_path, answers=[CLOUD, "me@x.com", "token", ""])

    out = capsys.readouterr().out
    assert "Basic email + API token" in out
    assert "REST API v3" in out


# --- ordinary behaviour -----------------------------------------------------


def test_enter_keeps_the_existing_value(monkeypatch, tmp_path):
    _, written = run(
        monkeypatch, tmp_path, answers=["", "", "", ""], existing=CLOUD_ENV
    )

    assert written == CLOUD_ENV


def test_a_trailing_slash_is_removed(monkeypatch, tmp_path):
    _, written = run(monkeypatch, tmp_path, answers=[SERVER + "/", "", "pat", ""])

    assert written["JIRA_BASE_URL"] == SERVER


def test_pasted_quotes_are_stripped(monkeypatch, tmp_path):
    _, written = run(monkeypatch, tmp_path, answers=[f'"{SERVER}"', "", '"pat"', ""])

    assert written["JIRA_BASE_URL"] == SERVER
    assert written["JIRA_API_TOKEN"] == "pat"


def test_the_token_is_never_echoed(monkeypatch, tmp_path, capsys):
    run(monkeypatch, tmp_path, answers=[SERVER, "", "secret-pat-value", ""])

    assert "secret-pat-value" not in capsys.readouterr().out


def test_an_existing_token_is_shown_masked(monkeypatch, tmp_path, capsys):
    prompts = []
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(f"JIRA_API_TOKEN=abcdefghijklmnop\n")

    replies = iter([SERVER, "", "", ""])

    def fake_input(prompt):
        prompts.append(prompt)
        return next(replies)

    monkeypatch.setattr("builtins.input", fake_input)
    setup.main()

    token_prompt = next(p for p in prompts if "JIRA_API_TOKEN" in p)
    assert "abcdefghijklmnop" not in token_prompt
    assert "16 characters" in token_prompt and "mnop" in token_prompt
