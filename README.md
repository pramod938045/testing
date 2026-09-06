# Jira Chatbot

A read-only chatbot for Jira Cloud. Ask questions in plain English — "what's assigned to me
and not done?", "summarise the current sprint", "what does DFE-9067 say?" — and it works out
which Jira API calls to make, runs them, and answers from the real data.

It cannot change anything in Jira. That is enforced in code (see below), not by a setting.

Claude does the language understanding via tool-calling; the Jira REST API does the work.
Three front ends — a browser chat UI, a Slack bot, and a terminal client — share one agent.

```
browser (app/main.py)  ─┐
Slack   (slack_bot.py) ─┼─> JiraChatAgent (app/agent.py)   tool-use loop with Claude
CLI     (cli.py)       ─┘      -> tools (app/tools.py)     9 read-only operations
                                  -> JiraClient (app/jira_client.py)  REST v3 + Agile v1.0
```

## What it can do

**It reads Jira; it never writes.** JQL search, issue detail (including the full
description), comments, projects, users, boards, sprints, and `sprint_report` — every issue
in a sprint plus counts by status and assignee, which the standup/blocker/status summaries
are built from.

**Read-only is enforced in code, not by configuration:**

- There are no create/update/transition/comment/worklog tools, so the model cannot ask for
  one — it can only call what it is given.
- `JiraClient` refuses any request that is not a `GET` (plus `POST` to the two search
  endpoints, since search reads but uses POST). The refusal happens before the request is
  built, so nothing reaches Jira.
- Tests assert both halves: no write-shaped tool is exposed, and each of create, edit,
  delete, comment, transition, worklog and reassign raises *and* never reaches the network.

The UI also lists every Jira call behind each answer, so you can see exactly what it read.

## Setup

Requires Python 3.11+.

```bash
pip install -r requirements.txt
cp .env.example .env      # then fill it in
```

You need two credentials:

| Variable | Where to get it |
| --- | --- |
| `JIRA_API_TOKEN` | https://id.atlassian.com/manage-profile/security/api-tokens |
| `ANTHROPIC_API_KEY` | https://console.anthropic.com/settings/keys |

The bot acts as the Atlassian account in `JIRA_EMAIL` and inherits exactly that account's
permissions — it cannot see or change anything that user couldn't. For a shared bot, use a
dedicated Atlassian account rather than a person's.

## Run it

Web UI at http://localhost:8000:

```bash
uvicorn app.main:app --reload
```

Slack (see setup below):

```bash
python slack_bot.py
```

Terminal:

```bash
python cli.py                                      # interactive
python cli.py "what's assigned to me this sprint?" # one-shot
```

As an API:

```bash
curl -s localhost:8000/api/chat \
  -H 'content-type: application/json' \
  -d '{"message": "show me open bugs in ABC"}'
# -> {"session_id": "...", "reply": "...", "tool_calls": [...]}
```

Pass the returned `session_id` back on the next call to continue the conversation.
`GET /api/health` reports Jira connectivity and the model in use.

## Things to ask it

- What's assigned to me and not done?
- Summarise the current sprint — who's overloaded?
- Which issues in ABC haven't been updated in two weeks?
- What does DFE-9067 actually say? Show me its full description.
- Show me the comments on ABC-42.
- Which statuses could ABC-42 move to? (it reports them; it cannot perform the move)

## Slack setup

The bot runs over **Socket Mode**, so it needs no public URL, no ngrok and no inbound
firewall rules — it dials out to Slack.

At https://api.slack.com/apps → **Create New App** → *From scratch*:

1. **Socket Mode** → enable it. That generates an **App-Level Token** with
   `connections:write` — this is `SLACK_APP_TOKEN` (`xapp-…`).
2. **OAuth & Permissions** → add these bot scopes:
   `app_mentions:read`, `chat:write`, `im:read`, `im:write`, `im:history`.
3. **Event Subscriptions** → enable, and subscribe to bot events:
   `app_mention` and `message.im`.
4. **Install to Workspace** → copy the **Bot User OAuth Token** into `SLACK_BOT_TOKEN`
   (`xoxb-…`).
5. `python slack_bot.py`, then invite the bot to a channel: `/invite @yourbot`.

How it behaves:

- **In a channel** — `@yourbot what's blocked in ABC?` It replies in a thread, and that
  thread is one conversation, so follow-ups keep context.
- **In a DM** — just type; no mention needed.
- **`@yourbot reset`** (or "new chat") starts the conversation over.
- Every reply carries a small context line listing the Jira calls it made.
- Markdown is converted to Slack formatting — tables become bullet lines, since Slack
  cannot render a table.

The bot is read-only in Slack too: it answers questions about Jira and cannot change
anything, whoever asks.

## Configuration

Every setting is an environment variable; see `.env.example` for the full list with
comments. The ones worth knowing:

| Variable | Default | Notes |
| --- | --- | --- |
| `JIRA_DEFAULT_PROJECT` | — | Assumed when the user doesn't name a project |
| `CLAUDE_MODEL` | `claude-opus-5` | |
| `CLAUDE_EFFORT` | `high` | `low`/`medium` are cheaper and faster |
| `MAX_TOOL_ITERATIONS` | `12` | Tool rounds per message before giving up |
| `HISTORY_TURNS` | `20` | Conversation turns kept in context |
| `SLACK_BOT_TOKEN` / `SLACK_APP_TOKEN` | — | Slack bot only |

## Tests

```bash
python -m pytest
```

99 tests, no network and no API spend: the Jira API is a `httpx.MockTransport`, Claude is a
stub that replays scripted tool-use responses, and the Slack client is a stub that records
what would have been posted. They cover the read-only guarantee (no write tool is exposed,
and every write verb is refused before it reaches the network), ADF reading, Jira error
handling, the agent loop (parallel tool calls, tool errors, runaway loops, history
trimming), session expiry and eviction, the Slack handlers, and the story generator.

## Adding a Jira operation

1. Add a **read** method to `JiraClient` returning a trimmed dict. A write will not work:
   `_request` refuses anything that is not a GET or a search POST.
2. Add a spec to `TOOL_SPECS` and an entry to `HANDLERS` in `app/tools.py`.
3. Add a test. `test_every_spec_has_a_handler_and_vice_versa` fails if you miss step 2, and
   `test_no_tool_can_change_jira` fails if the new tool is named like a write.

## Notes and limits

- **Jira Cloud only.** It uses REST API v3 (ADF rich text) and the Agile API. Jira Data
  Center/Server needs API v2, wiki-markup bodies and Bearer PAT auth — `jira_client.py` is
  where that would change.
- **Sessions are in memory**, capped at 200 and expiring after 4 hours. A restart clears
  them; for multiple server processes you'd move them to Redis.
- **No authentication on the web UI.** Everyone who can reach it acts as your Jira service
  account. Put it behind your SSO/proxy before exposing it beyond localhost. The same holds
  in Slack: anyone who can message the bot can read whatever that account can read.
- Search results are capped at 100 issues per call so a broad query can't exhaust the
  context window; the bot pages with `next_page_token` when it needs more.
