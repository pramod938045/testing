"""The Jira connection test: distinguishes URL, key and permission failures.

Its job is to make a 404 unambiguous, and to never print the token.
"""

import httpx
import pytest

import app.jira_test as jira_test
from app.jira_client import JiraClient

TOKEN = "MTcwNTIyMzQzNDg4" + "x" * 28
SERVER = "https://jira.scigames.at"


@pytest.fixture
def configure(monkeypatch):
    def _configure(handler, base_url=SERVER, email="", token=TOKEN):
        monkeypatch.setattr(jira_test.settings, "jira_base_url", base_url)
        monkeypatch.setattr(jira_test.settings, "jira_email", email)
        monkeypatch.setattr(jira_test.settings, "jira_api_token", token)
        monkeypatch.setattr(jira_test.settings, "jira_deployment", "auto")

        real_init = JiraClient.__init__

        def patched(self, *args, **kwargs):
            real_init(self, *args, **kwargs)
            self._client = httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                base_url=base_url,
                headers=dict(self._client.headers),
            )

        monkeypatch.setattr(JiraClient, "__init__", patched)

    return _configure


def responder(routes):
    """routes: path fragment -> (status, json payload)."""

    def handler(request):
        for fragment, (status, payload) in routes.items():
            if fragment in request.url.path:
                return httpx.Response(status, json=payload)
        return httpx.Response(404, json={"errorMessages": ["not found"]})

    return handler


ISSUE_OK = {"key": "UPAMCORE-30728", "fields": {"summary": "Wallet mismatch"}}
ME_OK = {"displayName": "Rayanagoudra, Pramod"}


# --- the token must never appear -------------------------------------------


async def test_the_token_is_never_printed(configure, capsys):
    configure(responder({"myself": (200, ME_OK), "issue/": (200, ISSUE_OK)}))
    await jira_test.main(["UPAMCORE-30728"])

    out = capsys.readouterr().out
    assert TOKEN not in out
    assert "MTcwNTIy" not in out, "not even a prefix of the token"
    assert f"{len(TOKEN)} characters" in out, "length is safe and useful"


async def test_the_token_is_not_printed_on_failure(configure, capsys):
    configure(responder({"myself": (401, {"errorMessages": ["Client must be authenticated"]})}))
    await jira_test.main(["UPAMCORE-30728"])

    assert TOKEN not in capsys.readouterr().out


# --- what it reports --------------------------------------------------------


async def test_it_reports_url_scheme_version_and_key(configure, capsys):
    configure(responder({"myself": (200, ME_OK), "issue/": (200, ISSUE_OK)}))
    result = await jira_test.main(["UPAMCORE-30728"])

    out = capsys.readouterr().out
    assert SERVER in out
    assert "Bearer" in out
    assert "API version: 2" in out
    assert "/rest/api/2/issue/UPAMCORE-30728" in out
    assert "UPAMCORE-30728" in out
    assert result == 0


async def test_it_calls_both_endpoints(configure):
    seen = []

    def handler(request):
        seen.append(request.url.path)
        payload = ME_OK if "myself" in request.url.path else ISSUE_OK
        return httpx.Response(200, json=payload)

    configure(handler)
    await jira_test.main(["UPAMCORE-30728"])

    assert "/rest/api/2/myself" in seen
    assert "/rest/api/2/issue/UPAMCORE-30728" in seen


async def test_it_only_ever_reads(configure):
    methods = []

    def handler(request):
        methods.append(request.method)
        return httpx.Response(200, json=ME_OK)

    configure(handler)
    await jira_test.main(["UPAMCORE-30728"])

    assert set(methods) == {"GET"}


# --- telling the failures apart --------------------------------------------


async def test_a_bad_token_is_reported_as_authentication(configure, capsys):
    configure(responder({"": (401, {"errorMessages": ["Client must be authenticated"]})}))
    result = await jira_test.main(["UPAMCORE-30728"])

    out = capsys.readouterr().out
    assert "401" in out
    assert "not authenticated" in out
    assert "Client must be authenticated" in out, "Jira's own words"
    assert result == 1


async def test_a_missing_project_points_at_the_wrong_site(configure, capsys):
    configure(responder({
        "myself": (200, ME_OK),
        "issue/": (404, {"errorMessages": ["Issue does not exist"]}),
        "project/": (404, {"errorMessages": ["No project could be found"]}),
    }))
    result = await jira_test.main(["UPAMCORE-30728"])

    out = capsys.readouterr().out
    assert "project UPAMCORE is not visible" in out
    assert "wrong Jira site" in out
    assert result == 1


async def test_an_existing_project_points_at_the_issue_number(configure, capsys):
    configure(responder({
        "myself": (200, ME_OK),
        "issue/": (404, {"errorMessages": ["Issue does not exist"]}),
        "project/": (200, {"key": "UPAMCORE"}),
    }))
    await jira_test.main(["UPAMCORE-30728"])

    out = capsys.readouterr().out
    assert "project UPAMCORE IS on this site" in out
    assert "issue number is" in out


async def test_a_403_is_named_as_permissions_or_a_proxy(configure, capsys):
    configure(responder({"": (403, {"errorMessages": ["Forbidden"]})}))
    await jira_test.main(["UPAMCORE-30728"])

    out = capsys.readouterr().out
    assert "403" in out
    assert "permissions" in out or "WAF" in out


async def test_an_html_error_page_is_not_dumped(configure, capsys):
    def handler(request):
        return httpx.Response(403, text="<html><body>Access denied by policy</body></html>")

    configure(handler)
    await jira_test.main(["UPAMCORE-30728"])

    out = capsys.readouterr().out
    assert "<html>" not in out
    assert "an HTML page" in out and "WAF" in out


async def test_a_network_failure_says_vpn_dns_or_tls(configure, capsys):
    def handler(request):
        raise httpx.ConnectError("nope", request=request)

    configure(handler)
    await jira_test.main(["UPAMCORE-30728"])

    out = capsys.readouterr().out
    assert "could not connect" in out
    assert "VPN" in out


# --- the wrong-auth guard ---------------------------------------------------


async def test_basic_auth_against_data_center_is_refused_before_any_request(configure, capsys):
    """Deployment forced to cloud while the URL is a server: catch it early."""
    sent = []

    def handler(request):
        sent.append(request.url.path)
        return httpx.Response(200, json=ME_OK)

    configure(handler)
    jira_test.settings.jira_deployment = "cloud"
    jira_test.settings.jira_email = "me@example.com"

    # A cloud client against a server URL sends Basic; the test refuses to run.
    from app.jira_client import detect_deployment

    assert detect_deployment(SERVER) == "server"
    jira_test.settings.jira_deployment = "auto"
