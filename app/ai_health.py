"""Health check for the Anthropic connection.

Verifies authentication with `models.list`, a GET that costs no tokens — so
this can be run freely without spending money. It never returns, logs or
echoes the API key: only whether it works, and safe facts about its shape.
"""

from __future__ import annotations

from typing import Any

import anthropic

from .config import api_key_report, settings


async def check_anthropic() -> dict[str, Any]:
    report = api_key_report()
    result: dict[str, Any] = {"key": report}

    if not report["key_in_use"]:
        result["anthropic"] = "no key"
        result["fix"] = (
            "ANTHROPIC_API_KEY is not set. Put it in "
            f"{report['dotenv_file']} and restart the server."
        )
        return result

    if report["looks_like_placeholder"]:
        result["anthropic"] = "placeholder key"
        result["fix"] = (
            "The key in use is example text, not a real key. Get one at "
            "https://console.anthropic.com/settings/keys"
        )
        return result

    if not report["prefix_looks_right"]:
        result["anthropic"] = "malformed key"
        result["fix"] = (
            "An Anthropic key starts with 'sk-ant-'. Check for pasted quotes, "
            "spaces, or a key from a different provider."
        )
        return result

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key, max_retries=1)
    try:
        # Authentication first, with a GET that costs nothing.
        await client.models.list(limit=1)
        result["key_valid"] = True

        # Then the question that actually matters: can this account generate?
        # models.list succeeds on an account with no credit, so checking auth
        # alone would report "ok" while every chat message still failed. This
        # costs one input and one output token — a fraction of a cent.
        await client.messages.create(
            model=settings.model,
            max_tokens=1,
            messages=[{"role": "user", "content": "hi"}],
        )
        result["anthropic"] = "ok"
        result["model"] = settings.model
    except anthropic.BadRequestError as exc:
        # A valid key on an account with no credit authenticates fine but
        # cannot generate, so say that rather than "bad request".
        if "credit balance" in str(exc).lower():
            result["anthropic"] = "no credit"
            result["fix"] = (
                "The key is valid, but the Anthropic account has no credit, so no "
                "question can be answered. Add funds under Plans & Billing at "
                "https://console.anthropic.com/settings/billing — or get added to an "
                "organisation that already has billing."
            )
        else:
            result["anthropic"] = "bad request (400)"
            result["fix"] = str(exc)[:200]
    except anthropic.NotFoundError:
        result["anthropic"] = "model not available"
        result["fix"] = (
            f"The account cannot use '{settings.model}'. Set CLAUDE_MODEL in .env to a "
            "model it has access to."
        )
    except anthropic.AuthenticationError:
        result["anthropic"] = "rejected (401)"
        result["fix"] = (
            "Anthropic rejected the key. Either it is wrong or revoked, or a stale "
            "ANTHROPIC_API_KEY in the shell is overriding your .env file — see "
            "shell_overrides_dotenv above. Open a new terminal and restart the server."
        )
    except anthropic.PermissionDeniedError:
        result["anthropic"] = "forbidden (403)"
        result["fix"] = "The key is valid but not permitted. Check the organisation's settings."
    except anthropic.RateLimitError:
        result["anthropic"] = "rate limited (429)"
        result["fix"] = "The key works, but you are being rate limited. Try again shortly."
    except anthropic.APIStatusError as exc:
        result["anthropic"] = f"api error ({exc.status_code})"
        if exc.status_code == 400 and "credit" in str(exc).lower():
            result["fix"] = "The account has no credit. Add billing in the Anthropic console."
    except anthropic.APIConnectionError:
        result["anthropic"] = "unreachable"
        result["fix"] = "Could not reach api.anthropic.com. Check the network, VPN or proxy."
    finally:
        await client.close()

    if result.get("anthropic") != "ok" and report["shell_overrides_dotenv"]:
        result["warning"] = (
            "A shell ANTHROPIC_API_KEY is overriding the one in your .env file. "
            "Close the terminal, open a new one, and start the server again."
        )
    return result
