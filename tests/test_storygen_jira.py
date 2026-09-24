import httpx
import pytest

from storygen.config import Config, ConfigError
from storygen.jira import Jira, JiraError


def make_jira(handler) -> Jira:
    jira = Jira("https://example.atlassian.net", "me@example.com", "token")
    jira.http = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://example.atlassian.net"
    )
    return jira


def test_myself_returns_the_account():
    def handler(request):
        assert request.url.path == "/rest/api/3/myself"
        return httpx.Response(200, json={"displayName": "Sam Patel", "accountId": "5b10a"})

    jira = make_jira(handler)
    assert jira.myself()["displayName"] == "Sam Patel"
    jira.close()


def test_auth_failure_explains_which_settings_to_check():
    jira = make_jira(lambda r: httpx.Response(401, json={"errorMessages": ["Unauthorized"]}))
    with pytest.raises(JiraError) as exc:
        jira.myself()

    assert "401" in str(exc.value)
    assert "JIRA_API_TOKEN" in str(exc.value)
    jira.close()


def test_config_lists_every_missing_setting():
    """Cloud needs the email too, so all three names appear."""
    with pytest.raises(ConfigError) as exc:
        Config(
            jira_url="https://example.atlassian.net",
            jira_email="",
            jira_token="",
            anthropic_key="",
        ).check_jira()

    message = str(exc.value)
    assert "JIRA_EMAIL" in message and "JIRA_API_TOKEN" in message


def test_data_center_config_does_not_require_an_email():
    """A Data Center personal access token authenticates on its own."""
    Config(
        jira_url="https://jira.example.com",
        jira_email="",
        jira_token="pat",
        anthropic_key="",
    ).check_jira()


def test_missing_url_and_token_are_both_reported():
    with pytest.raises(ConfigError) as exc:
        Config(jira_url="", jira_email="", jira_token="", anthropic_key="").check_jira()

    message = str(exc.value)
    assert "JIRA_BASE_URL" in message and "JIRA_API_TOKEN" in message


def test_config_with_jira_settings_passes_without_an_ai_key():
    Config(
        jira_url="https://example.atlassian.net",
        jira_email="me@example.com",
        jira_token="t",
        anthropic_key="",
    ).check_jira()
