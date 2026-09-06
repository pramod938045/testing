# Jira Chatbot

A chatbot for Jira Cloud. Ask questions in plain English — "what's assigned to me and not
done?", "summarise the current sprint", "log 2h on ABC-42" — and it works out which Jira
API calls to make, runs them, and answers from the real data.

Claude does the language understanding via tool-calling; the Jira REST API does the work.
Three front ends — a browser chat UI, a Slack bot, and a terminal client — share one agent.

```
browser (app/main.py)  ─┐
Slack   (slack_bot.py) ─┼─> JiraChatAgent (app/agent.py)   tool-use loop with Claude
CLI     (cli.py)       ─┘      -> tools (app/tools.py)     15 Jira operations
                                  -> JiraClient (app/jira_client.py)  REST v3 + Agile v1.0
```

## What it can do

**Read** — JQL search, issue detail, comments, projects, users, issue types, boards, sprints.
**Write** — create issues, update fields (summary, description, assignee, priority, labels,
due date), transition status, comment, log work.
**Report** — `sprint_report` returns every issue in a sprint plus counts by status and by
assignee, which is what the standup/blocker/status summaries are built from.

Two guards on the write side:

- `JIRA_ALLOW_WRITES=false` removes the write tools from the request entirely — the model
  cannot call what it isn't given.
- With writes on, the system prompt requires the bot to restate the change and get a "yes"
  before the first write of a request. The UI also shows every Jira call it made, so a
  change is never invisible.

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
`GET /api/health` reports Jira connectivity, the model in use and whether writes are on.

## Things to ask it

- What's assigned to me and not done?
- Summarise the current sprint — who's overloaded?
- Which issues in ABC haven't been updated in two weeks?
- Show me ABC-42 and its comments.
- Create a bug in ABC: "checkout 500s on submit", high priority, assign to Priya.
- Move ABC-42 to In Progress and comment that I've started.
- Log 3h on ABC-42 for the migration work.

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

The write confirmation still applies: it asks before creating, updating, transitioning,
commenting or logging work — in the thread, where your team can see it.

## Configuration

Every setting is an environment variable; see `.env.example` for the full list with
comments. The ones worth knowing:

| Variable | Default | Notes |
| --- | --- | --- |
| `JIRA_ALLOW_WRITES` | `true` | `false` = read-only bot |
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

60 tests, no network and no API spend: the Jira API is a `httpx.MockTransport`, Claude is a
stub that replays scripted tool-use responses, and the Slack client is a stub that records
what would have been posted. They cover ADF conversion, request shaping for every write
path, error handling, the write guard, the agent loop (parallel tool calls, tool errors,
runaway loops, history trimming), session expiry and eviction, and the Slack handlers.

## Adding a Jira operation

1. Add a method to `JiraClient` returning a trimmed dict.
2. Add a spec to `TOOL_SPECS` and an entry to `HANDLERS` in `app/tools.py` — put the name in
   `WRITE_TOOLS` if it changes anything.
3. Add a test. `test_every_spec_has_a_handler_and_vice_versa` will fail if you miss step 2.

## Notes and limits

- **Jira Cloud only.** It uses REST API v3 (ADF rich text) and the Agile API. Jira Data
  Center/Server needs API v2, wiki-markup bodies and Bearer PAT auth — `jira_client.py` is
  where that would change.
- **Sessions are in memory**, capped at 200 and expiring after 4 hours. A restart clears
  them; for multiple server processes you'd move them to Redis.
- **No authentication on the web UI.** Everyone who can reach it acts as your Jira service
  account. Put it behind your SSO/proxy before exposing it beyond localhost. The same holds
  in Slack: anyone who can message the bot gets that account's Jira access, so consider
  `JIRA_ALLOW_WRITES=false` for a bot in a wide channel.
- Search results are capped at 100 issues per call so a broad query can't exhaust the
  context window; the bot pages with `next_page_token` when it needs more.
