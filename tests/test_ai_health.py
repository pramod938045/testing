"""Anthropic health check: reports whether auth works, never leaks the key."""

import anthropic
import httpx
import pytest

import app.ai_health as ai_health
from app.config import api_key_report

REAL_LOOKING = "sk-ant-api03-" + "x" * 90


def set_key(monkeypatch, value, in_dotenv=None, dotenv_path="/repo/.env"):
    """Point the module at a given live key, and optionally a differing .env."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", value)
    monkeypatch.setattr(ai_health.settings, "anthropic_api_key", value)
    monkeypatch.setattr("app.config.DOTENV_PATH", dotenv_path)
    monkeypatch.setattr(
        "app.config.dotenv_values",
        lambda path: {"ANTHROPIC_API_KEY": in_dotenv} if in_dotenv else {},
    )


def fake_client(error=None):
    class Models:
        async def list(self, **kwargs):
            if error:
                raise error
            return object()

    class Client:
        def __init__(self, **kwargs):
            self.models = Models()

        async def close(self):
            pass

    return Client


def api_error(cls, status):
    request = httpx.Request("GET", "https://api.anthropic.com/v1/models")
    response = httpx.Response(status, request=request, json={"error": {"message": "nope"}})
    return cls("nope", response=response, body=None)


# --- key reporting ---------------------------------------------------------


def test_report_never_contains_the_key(monkeypatch):
    set_key(monkeypatch, REAL_LOOKING)
    report = api_key_report()

    flat = repr(report)
    assert REAL_LOOKING not in flat
    assert "sk-ant-api03" not in flat
    assert report["key_length"] == len(REAL_LOOKING)
    assert report["prefix_looks_right"] is True


def test_report_spots_a_shell_variable_overriding_the_dotenv_file(monkeypatch):
    set_key(monkeypatch, "sk-ant-from-the-shell-window", in_dotenv=REAL_LOOKING)
    report = api_key_report()

    assert report["shell_overrides_dotenv"] is True
    assert report["key_in_dotenv"] is True


def test_report_is_quiet_when_the_dotenv_value_is_the_one_in_use(monkeypatch):
    set_key(monkeypatch, REAL_LOOKING, in_dotenv=REAL_LOOKING)
    assert api_key_report()["shell_overrides_dotenv"] is False


# --- the check itself ------------------------------------------------------


async def test_working_key_reports_ok(monkeypatch):
    set_key(monkeypatch, REAL_LOOKING)
    monkeypatch.setattr(anthropic, "AsyncAnthropic", fake_client())

    result = await ai_health.check_anthropic()
    assert result["anthropic"] == "ok"
    assert "fix" not in result


async def test_missing_key_is_reported_without_calling_the_api(monkeypatch):
    set_key(monkeypatch, "")
    called = False

    def boom(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("must not call the API without a key")

    monkeypatch.setattr(anthropic, "AsyncAnthropic", boom)
    result = await ai_health.check_anthropic()

    assert result["anthropic"] == "no key"
    assert called is False


async def test_placeholder_key_is_named_as_such(monkeypatch):
    set_key(monkeypatch, "sk-ant-your-key-here")
    result = await ai_health.check_anthropic()

    assert result["anthropic"] == "placeholder key"
    assert "console.anthropic.com" in result["fix"]


async def test_malformed_key_is_caught_before_a_request(monkeypatch):
    set_key(monkeypatch, "sk-proj-" + "y" * 90)
    result = await ai_health.check_anthropic()

    assert result["anthropic"] == "malformed key"
    assert "sk-ant-" in result["fix"]


async def test_rejected_key_explains_the_shell_override(monkeypatch):
    """The exact failure the user hit: a stale shell key beating .env."""
    set_key(monkeypatch, REAL_LOOKING, in_dotenv="sk-ant-api03-" + "z" * 90)
    monkeypatch.setattr(
        anthropic, "AsyncAnthropic", fake_client(api_error(anthropic.AuthenticationError, 401))
    )
    result = await ai_health.check_anthropic()

    assert result["anthropic"] == "rejected (401)"
    assert "shell_overrides_dotenv" in result["fix"]
    assert "overriding" in result["warning"] and "open a new one" in result["warning"]


@pytest.mark.parametrize(
    "error,expected",
    [
        (api_error(anthropic.PermissionDeniedError, 403), "forbidden (403)"),
        (api_error(anthropic.RateLimitError, 429), "rate limited (429)"),
        (api_error(anthropic.InternalServerError, 500), "api error (500)"),
        (anthropic.APIConnectionError(request=httpx.Request("GET", "https://x")), "unreachable"),
    ],
)
async def test_each_failure_kind_is_named(monkeypatch, error, expected):
    set_key(monkeypatch, REAL_LOOKING)
    monkeypatch.setattr(anthropic, "AsyncAnthropic", fake_client(error))

    assert (await ai_health.check_anthropic())["anthropic"] == expected


async def test_no_failure_path_leaks_the_key(monkeypatch):
    set_key(monkeypatch, REAL_LOOKING)
    for error in (
        api_error(anthropic.AuthenticationError, 401),
        api_error(anthropic.RateLimitError, 429),
        anthropic.APIConnectionError(request=httpx.Request("GET", "https://x")),
        None,
    ):
        monkeypatch.setattr(anthropic, "AsyncAnthropic", fake_client(error))
        assert REAL_LOOKING not in repr(await ai_health.check_anthropic())


async def test_the_check_costs_no_tokens(monkeypatch):
    """It must authenticate with models.list, never by generating a message."""
    set_key(monkeypatch, REAL_LOOKING)
    used = []

    class Models:
        async def list(self, **kwargs):
            used.append("models.list")
            return object()

    class Messages:
        async def create(self, **kwargs):
            raise AssertionError("the health check must not spend tokens")

    class Client:
        def __init__(self, **kwargs):
            self.models, self.messages = Models(), Messages()

        async def close(self):
            pass

    monkeypatch.setattr(anthropic, "AsyncAnthropic", Client)
    await ai_health.check_anthropic()
    assert used == ["models.list"]
