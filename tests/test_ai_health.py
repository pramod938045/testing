"""Anthropic health check: reports whether auth works, never leaks the key."""

import anthropic
import httpx
import pytest

import app.ai_health as ai_health
import app.config
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
    # Since .env now wins, "in_dotenv" means the file supplied the live value.
    monkeypatch.setitem(
        app.config.ENV_FILE, "names", ["ANTHROPIC_API_KEY"] if in_dotenv else []
    )
    monkeypatch.setitem(app.config.ENV_FILE, "shadowed", [])


def fake_client(error=None, auth_error=None, calls=None):
    """A stub client. `auth_error` fails models.list; `error` fails messages.create."""

    class Models:
        async def list(self, **kwargs):
            if calls is not None:
                calls.append("models.list")
            if auth_error:
                raise auth_error
            return object()

    class Messages:
        async def create(self, **kwargs):
            if calls is not None:
                calls.append("messages.create")
            if error:
                raise error
            return object()

    class Client:
        def __init__(self, **kwargs):
            self.models, self.messages = Models(), Messages()

        async def close(self):
            pass

    return Client


def api_error(cls, status, message="nope"):
    request = httpx.Request("GET", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status, request=request, json={"error": {"message": message}})
    return cls(message, response=response, body=None)


# --- key reporting ---------------------------------------------------------


def test_report_never_contains_the_key(monkeypatch):
    set_key(monkeypatch, REAL_LOOKING)
    report = api_key_report()

    flat = repr(report)
    assert REAL_LOOKING not in flat
    assert "sk-ant-api03" not in flat
    assert report["key_length"] == len(REAL_LOOKING)
    assert report["prefix_looks_right"] is True


def test_the_report_says_the_key_came_from_the_dotenv_file(monkeypatch):
    set_key(monkeypatch, REAL_LOOKING, in_dotenv=REAL_LOOKING)
    report = api_key_report()

    assert report["source"] == "the .env file"
    assert report["key_in_dotenv"] is True


def test_the_report_says_when_the_key_came_from_the_shell(monkeypatch):
    """No .env entry, so the value can only have come from the environment."""
    set_key(monkeypatch, REAL_LOOKING)

    assert api_key_report()["source"] == "the shell environment"


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


async def test_a_rejected_key_names_where_it_came_from(monkeypatch):
    """A rejected key is only actionable if you know which key was tried."""
    set_key(monkeypatch, REAL_LOOKING)
    monkeypatch.setattr(
        anthropic,
        "AsyncAnthropic",
        fake_client(auth_error=api_error(anthropic.AuthenticationError, 401)),
    )
    result = await ai_health.check_anthropic()

    assert result["anthropic"] == "rejected (401)"
    assert "the shell environment" in result["fix"]
    assert "app.setup" in result["warning"]


async def test_a_rejected_key_from_the_file_does_not_blame_the_shell(monkeypatch):
    set_key(monkeypatch, REAL_LOOKING, in_dotenv=REAL_LOOKING)
    monkeypatch.setattr(
        anthropic,
        "AsyncAnthropic",
        fake_client(auth_error=api_error(anthropic.AuthenticationError, 401)),
    )
    result = await ai_health.check_anthropic()

    assert "the .env file" in result["fix"]
    assert "warning" not in result


async def test_a_valid_key_with_no_credit_says_so(monkeypatch):
    """The account authenticates but cannot generate — the real blocker here."""
    set_key(monkeypatch, REAL_LOOKING)
    error = api_error(
        anthropic.BadRequestError, 400, "Your credit balance is too low to access the API."
    )
    monkeypatch.setattr(anthropic, "AsyncAnthropic", fake_client(error))

    result = await ai_health.check_anthropic()
    assert result["anthropic"] == "no credit"
    assert "billing" in result["fix"].lower()


@pytest.mark.parametrize(
    "error,expected",
    [
        (api_error(anthropic.PermissionDeniedError, 403), "forbidden (403)"),
        (api_error(anthropic.RateLimitError, 429), "rate limited (429)"),
        (api_error(anthropic.InternalServerError, 500), "api error (500)"),
        (api_error(anthropic.NotFoundError, 404), "model not available"),
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


async def test_the_check_verifies_auth_then_generation(monkeypatch):
    """models.list succeeds with no credit, so auth alone is not enough.

    The check must also attempt a generation, or it reports "ok" for an
    account that cannot answer a single question.
    """
    set_key(monkeypatch, REAL_LOOKING)
    calls = []
    monkeypatch.setattr(anthropic, "AsyncAnthropic", fake_client(calls=calls))

    await ai_health.check_anthropic()
    assert calls == ["models.list", "messages.create"]


async def test_the_generation_probe_is_one_token(monkeypatch):
    """Kept minimal: the check must never cost more than a fraction of a cent."""
    set_key(monkeypatch, REAL_LOOKING)
    sent = {}

    class Messages:
        async def create(self, **kwargs):
            sent.update(kwargs)
            return object()

    class Client:
        def __init__(self, **kwargs):
            self.messages = Messages()
            self.models = type("M", (), {"list": lambda s, **k: _done()})()

        async def close(self):
            pass

    async def _noop():
        return object()

    def _done():
        return _noop()

    monkeypatch.setattr(anthropic, "AsyncAnthropic", Client)
    await ai_health.check_anthropic()
    assert sent["max_tokens"] == 1
