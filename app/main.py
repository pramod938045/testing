"""FastAPI app: a chat UI and a JSON API in front of the Jira agent."""

from __future__ import annotations

import logging
import re
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

import anthropic
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .agent import JiraChatAgent
from .ai_health import check_anthropic
from .config import ConfigError, settings
from .jira_client import JiraClient, JiraError
from .sessions import SessionStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*-\d+$")

state: dict[str, Any] = {"jira": None, "jira_user": {}}
sessions: SessionStore[JiraChatAgent] = SessionStore(
    factory=lambda: JiraChatAgent(jira=state["jira"], settings=settings, jira_user=state["jira_user"])
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate()
    jira = JiraClient(
        base_url=settings.jira_base_url,
        email=settings.jira_email,
        api_token=settings.jira_api_token,
        timeout=settings.jira_timeout_seconds,
    )
    state["jira"] = jira
    try:
        state["jira_user"] = await jira.myself()
        logger.info("Connected to Jira as %s", state["jira_user"].get("display_name"))
    except JiraError as exc:
        # Don't refuse to boot: surface the problem via /api/health instead.
        logger.error("Jira connection check failed: %s", exc.message)
        state["jira_user"] = {}
    yield
    await jira.aclose()


app = FastAPI(title="Jira Chatbot", version="1.0.0", lifespan=lifespan)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    session_id: str | None = None


class SessionRequest(BaseModel):
    session_id: str | None = None


class ToolCallOut(BaseModel):
    name: str
    args: dict[str, Any]
    ok: bool
    summary: str


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    tool_calls: list[ToolCallOut]


def _get_agent(session_id: str | None) -> tuple[str, JiraChatAgent]:
    key = session_id or uuid.uuid4().hex
    return key, sessions.get(key)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/lookup")
async def lookup_page() -> FileResponse:
    """Ticket lookup — reads Jira directly, needs no AI key."""
    return FileResponse(STATIC_DIR / "lookup.html")


@app.get("/api/issue/{key}")
async def get_issue(key: str) -> dict[str, Any]:
    jira: JiraClient | None = state.get("jira")
    if jira is None:
        raise HTTPException(status_code=503, detail="Jira client is not configured.")

    key = key.strip().upper()
    if not ISSUE_KEY_RE.match(key):
        raise HTTPException(
            status_code=400, detail=f"'{key}' is not an issue key. Try something like DFE-9067."
        )
    try:
        return await jira.get_issue_full(key)
    except JiraError as exc:
        status = 404 if exc.status_code == 404 else 502
        raise HTTPException(status_code=status, detail=exc.message)


@app.get("/api/health/anthropic")
async def anthropic_health() -> dict[str, Any]:
    """Is the Anthropic connection working? Never reveals the key itself."""
    return await check_anthropic()


@app.get("/api/health")
async def health() -> dict[str, Any]:
    jira: JiraClient | None = state.get("jira")
    result: dict[str, Any] = {
        "jira_base_url": settings.jira_base_url,
        "model": settings.model,
        "read_only": True,
        "active_sessions": len(sessions),
    }
    if jira is None:
        result["jira"] = "not configured"
    else:
        try:
            user = await jira.myself()
            result["jira"] = "ok"
            result["jira_user"] = user.get("display_name")
        except JiraError as exc:
            result["jira"] = f"error: {exc.message}"
    result["anthropic_api_key"] = "set" if settings.anthropic_api_key else "missing"
    return result


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    if state.get("jira") is None:
        raise HTTPException(status_code=503, detail="Jira client is not configured.")

    # Without a key the SDK raises a TypeError deep inside the request, which
    # would surface as a plain-text 500 the browser cannot parse. Say it plainly.
    if not settings.anthropic_api_key:
        raise HTTPException(
            status_code=503,
            detail=(
                "No Anthropic API key is configured, so I cannot answer questions. "
                "Run `python -m app.setup` to add one, then restart the server. "
                "Ticket lookup at /lookup works without a key."
            ),
        )

    session_id, agent = _get_agent(request.session_id)
    try:
        result = await agent.chat(request.message.strip())
    except anthropic.AuthenticationError:
        raise HTTPException(status_code=502, detail="Claude rejected the API key. Check ANTHROPIC_API_KEY.")
    except anthropic.RateLimitError:
        raise HTTPException(status_code=429, detail="Claude is rate limiting. Try again shortly.")
    except anthropic.BadRequestError as exc:
        if "credit balance" in str(exc).lower():
            raise HTTPException(
                status_code=402,
                detail=(
                    "The Anthropic account has no credit, so no question can be answered. "
                    "Add funds at https://console.anthropic.com/settings/billing. "
                    "Ticket lookup at /lookup works without credit."
                ),
            )
        raise HTTPException(status_code=502, detail=f"Claude rejected the request: {exc}"[:300])
    except anthropic.APIStatusError as exc:
        logger.exception("Claude API error")
        raise HTTPException(status_code=502, detail=f"Claude API error ({exc.status_code}).")
    except anthropic.APIConnectionError:
        raise HTTPException(status_code=502, detail="Could not reach the Claude API.")
    except Exception:
        # Nothing may escape as a plain-text 500: the page parses JSON.
        logger.exception("Unexpected failure answering a chat message")
        raise HTTPException(
            status_code=500,
            detail="Something went wrong answering that. The server window shows the detail.",
        )

    return ChatResponse(
        session_id=session_id,
        reply=result.reply,
        tool_calls=[ToolCallOut(**asdict(call)) for call in result.tool_calls],
    )


@app.post("/api/reset")
async def reset(request: SessionRequest | None = None) -> dict[str, str]:
    session_id = request.session_id if request else None
    if session_id:
        sessions.pop(session_id)
    return {"status": "cleared"}


@app.exception_handler(ConfigError)
async def config_error_handler(_request, exc: ConfigError):  # pragma: no cover - startup guard
    raise HTTPException(status_code=500, detail=str(exc))
