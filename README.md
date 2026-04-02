# BEEtexting <-> Microsoft Teams <-> Zoho Desk Bridge

This project bridges customer SMS conversations (BEEtexting) into Microsoft Teams and allows agents to:
- Reply from Teams back to SMS (using `@rc-zd-teams-bridge` mention in the channel thread)
- Create Zoho Desk tickets from selected conversation ranges

## Current Status (April 1, 2026

### WORKING end-to-end as of March 31, 2026

- Inbound SMS from any number → appears as an Adaptive Card in Teams channel (one thread per customer number)
- Agent replies in Teams thread using `@rc-zd-teams-bridge <message>` → SMS delivered to customer via BEEtexting
- Each customer phone number gets its own persistent thread in the channel
- Zoho Desk ticket creation working (Create Ticket button → modal → ticket created with transcript)

### In progress

- EC2 instance provisioned (Ubuntu 24.04 LTS, t3.medium, Elastic IP **54.153.64.137**) — awaiting domain DNS + Docker deploy
- `deploy/` folder is the self-contained Docker package for EC2 (see [Deployment Package](#deployment-package) below)

### What is fully implemented

- BEEtexting inbound webhook receiver (`GET/POST /webhook`)
- BEEtexting send/fetch/token flows
- Teams Bot Framework endpoint (`POST /api/messages`)
- SQLite message/thread store (`src/message_store.py`):
  - inbound/outbound message history
  - phone → Teams thread mapping
  - Teams `service_url` state
- Proactive Teams posting (`src/teams_client.py`):
  - create thread per new phone number
  - continue existing thread for known phone number
- Teams activity handling (`src/teams_bot.py`):
  - agent `@mention` replies in Teams → SMS send via BEEtexting
  - Adaptive Card task/fetch and task/submit flow
- Task Module HTML UI (`static/create_ticket.html`) and conversation API (`GET /api/conversation/messages`)
- Zoho Desk ticket creation client (`src/zoho_desk.py`) with refresh-token auth and department resolution
- Static legal pages: `static/privacy.html`, `static/terms.html`

---

## Account & Infrastructure Map

All account IDs, resource IDs, phone numbers, and infrastructure details are in `credentials/accounts.md` (gitignored — never committed).

Resources involved:
- **Azure Bot**: `rc-zd-teams-bridge-bot-ans-global` (portal.azure.com, personal account)
- **Teams App**: `rc-zd-teams-bridge` v1.0.3, installed in Webzter IT Solutions org
- **BEEtexting**: app.beetexting.com (webhook subscription ID in `beetexting_subscription_id.txt`)
- **Zoho Desk**: desk.zoho.com (auth via refresh token in `.env`)
- **EC2**: t3.medium Ubuntu 24.04 LTS — Elastic IP in `credentials/accounts.md`

**Messaging endpoint** (update when URL changes): `https://<url>/api/messages` in Azure Bot → Configuration.

Once EC2 is live, the Cloudflare tunnel and all its URL churn goes away permanently.

### Cloudflare Tunnel (temporary, used until EC2 is live)

The local server is currently exposed via a Cloudflare quick tunnel. **Every time the tunnel restarts, the URL changes** and three things must be updated:

1. Azure Bot messaging endpoint → `https://<new-url>/api/messages`
2. BEEtexting webhook subscription → delete old, create new pointing to `https://<new-url>/webhook`
3. Teams app manifest `validDomains` + developer URLs → rebuild zip and upload to Admin Center

See [Tunnel URL Change Runbook](#tunnel-url-change-runbook) below.

---

## Runtime Flow

```text
Inbound SMS:
  Customer texts +19494248180
    → BEEtexting webhook POST /webhook
      → fetch full message from BEEtexting API
      → store in SQLite (messages table)
      → look up existing thread for this phone number (threads table)
        ├─ [new number] create_conversation() → new Teams thread → store conversation_id
        └─ [known number] continue_conversation() → reply in existing thread
      → post Adaptive Card in Teams thread (SMS text + "Create Ticket" button)

Agent reply in Teams:
  Agent types "@rc-zd-teams-bridge <message>" in thread
    → Teams → POST /api/messages
      → map conversation_id → phone number (threads table)
      → strip @mention from text
      → send SMS via BEEtexting
      → store outbound message in SQLite
      → Teams confirms "SMS sent to <number>"

Create Ticket:
  Agent clicks [Create Ticket] → task/fetch invoke
    → open /task-module/create-ticket?phone=+1xxx (HTML modal)
      → select message range + type subject
      → task/submit
        → fetch messages from SQLite between from_id and to_id
        → create Zoho Desk ticket with transcript
        → post "Ticket #1234 created" confirmation in Teams thread
```

### Important: @mention required for agent replies

In a Teams **channel**, messages are only routed to the bot when the bot is **@mentioned**. Agents must start replies with:

```
@rc-zd-teams-bridge Your message here
```

The `@rc-zd-teams-bridge` prefix is automatically stripped before the SMS is sent to the customer.

---

## API Surface

- `GET /webhook` — BEEtexting validation handshake
- `POST /webhook` — inbound SMS notification from BEEtexting
- `POST /api/messages` — Teams Bot Framework endpoint
- `GET /task-module/create-ticket` — serves the ticket creation HTML modal
- `GET /api/conversation/messages` — message history for a phone number (used by modal)

---

## Key Files

| File | Purpose |
|---|---|
| `src/beetexting_webhook.py` | FastAPI app — all HTTP endpoints |
| `src/teams_client.py` | Proactive Teams thread creation/continuation |
| `src/teams_bot.py` | Teams activity handler (replies, task module) |
| `src/message_store.py` | SQLite persistence layer |
| `src/zoho_desk.py` | Zoho Desk ticket client |
| `src/beetexting_send_sms.py` | Send SMS via BEEtexting API |
| `src/beetexting_subscribe.py` | Manage BEEtexting webhook subscriptions |
| `static/create_ticket.html` | Task Module UI |
| `teams_manifest/manifest.json` | Teams app manifest source (replace YOUR_DOMAIN before packaging) |
| `bridge.db` | SQLite database (auto-created, gitignored) |
| `beetexting_subscription_id.txt` | Current BEEtexting webhook subscription ID |
| `Dockerfile` | Docker image definition |
| `docker-compose.yml` | Orchestrates app + nginx + certbot |
| `nginx/default.conf` | Reverse proxy config (replace YOUR_DOMAIN before deploy) |

---

## Environment Variables

```env
# BEEtexting core (M2M client credentials)
BEETEXTING_CLIENT_ID=
BEETEXTING_CLIENT_SECRET=
BEETEXTING_API_KEY=
BEETEXTING_ORG_ID=
BEETEXTING_DEPT_ID=

# BEEtexting user auth (for webhook subscription management)
BEETEXTING_USER_CLIENT_ID=
BEETEXTING_USER_CLIENT_SECRET=
BEETEXTING_USER_API_KEY=

# Teams bot
TEAMS_APP_ID=                    # 9185bafc-d753-4c30-935c-f6bb70449baf
TEAMS_APP_PASSWORD=              # client secret from Azure App Registration
TEAMS_TENANT_ID=                 # eaa017ab-5443-42df-a2fa-8cf876069884
TEAMS_CHANNEL_ID=                # 19:CisU706ORy7BoXfKktwXkK32KdPv-i5MnszLs_Ro-t01@thread.tacv2

# Teams metadata (operational tracking)
TEAMS_APP_OBJECT_ID=
TEAMS_APP_DISPLAY_NAME=
TEAMS_APP_SECRET_ID=
TEAMS_APP_SECRET_EXPIRES_ON=

# Public URL (Cloudflare quick tunnel — update when tunnel URL changes)
PUBLIC_BASE_URL=
TASK_MODULE_BASE_URL=

# Zoho Desk
ZOHO_CLIENT_ID=
ZOHO_CLIENT_SECRET=
ZOHO_REFRESH_TOKEN=
ZOHO_DESK_ORG_ID=
ZOHO_DESK_BASE=https://desk.zoho.com
ZOHO_ACCOUNTS_TOKEN_URL=https://accounts.zoho.com/oauth/v2/token
ZOHO_DESK_DEPARTMENT_ID=
```

---

## Local Run

**Terminal 1 — server:**
```bash
uv run python src/beetexting_webhook.py --port 8000
```

**Terminal 2 — Cloudflare tunnel:**
```bash
cloudflared tunnel --url http://localhost:8000
```

Note the tunnel URL printed (e.g. `https://something-random.trycloudflare.com`) and follow the [Tunnel URL Change Runbook](#tunnel-url-change-runbook).

---

## Tunnel URL Change Runbook

Every time the Cloudflare tunnel restarts, do these steps in order:

**1. Update `.env`:**
```
PUBLIC_BASE_URL=https://<new-url>
TASK_MODULE_BASE_URL=https://<new-url>
```

**2. Update Azure Bot messaging endpoint:**
- portal.azure.com → `rc-zd-teams-bridge-bot-ans-global` → Configuration
- Set Messaging endpoint to `https://<new-url>/api/messages` → Apply

**3. Update BEEtexting webhook subscription:**
```bash
# Get current subscription ID from beetexting_subscription_id.txt, then:
uv run python src/beetexting_subscribe.py --delete --id <current-id>
uv run python src/beetexting_subscribe.py --create --webhook-url "https://<new-url>/webhook"
```

**4. Update Teams app manifest and re-upload:**
- Edit `teams_manifest/manifest.json`: update `validDomains` and developer URLs, bump `version`
- Repackage: `cd teams_manifest && zip ../rc-zd-teams-bridge.zip manifest.json color.png outline.png && cd ..`
- Teams Admin Center → Manage apps → `rc-zd-teams-bridge` → Upload file → upload new zip

**5. Restart the server** to pick up the new `.env`.

---

## SQLite Schema

```sql
CREATE TABLE messages (
    id          TEXT PRIMARY KEY,   -- BEEtexting message ID
    timestamp   INTEGER NOT NULL,   -- Unix ms
    direction   TEXT NOT NULL,      -- 'inbound' or 'outbound'
    from_number TEXT NOT NULL,
    to_number   TEXT NOT NULL,
    text        TEXT NOT NULL
);

CREATE TABLE threads (
    phone_number     TEXT PRIMARY KEY,
    conversation_id  TEXT NOT NULL,  -- Teams thread conversation ID
    service_url      TEXT NOT NULL,  -- Teams region-specific service URL
    created_at       INTEGER NOT NULL
);

CREATE TABLE bot_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- bot_state stores: service_url (captured from first Teams activity)
```

---

## SDK Notes

Uses **`BotFrameworkAdapter`** (not `CloudAdapter`) because `CloudAdapter.create_connector_client` has a confirmed bug (GitHub issue #2061) that breaks `create_conversation()` needed for new thread creation. The botbuilder-* family is deprecated but fully functional.

---

## Known Quirks

1. **@mention required**: In Teams channels, agents must @mention `@rc-zd-teams-bridge` to have their reply sent as SMS. The mention is stripped before the SMS is sent.
2. **Tunnel URL churn**: Cloudflare quick tunnels get a new URL on every restart. Follow the runbook above each time.
3. **`service_url` must be captured first**: The bot cannot post proactively until at least one Teams activity has been received (which stores the `service_url` in `bot_state`). This happens automatically on app install.
4. **Teams app cache**: The Teams client caches app version display; the actual installed package is always the latest uploaded to Admin Center regardless of what version the UI shows.

---

## EC2 Deployment

This repo IS the deployable package — no separate folder needed. Docker infrastructure files are at root:

```
Dockerfile
docker-compose.yml
nginx/default.conf        ← replace YOUR_DOMAIN before deploy
requirements.txt
.env.example              ← copy to .env and fill in values
teams_manifest/           ← replace YOUR_DOMAIN in manifest.json before packaging
```

To deploy to EC2 (54.153.64.137):
1. Point your domain's DNS A record at `54.153.64.137`
2. Clone this repo on EC2, fill in `.env`, replace `YOUR_DOMAIN` in nginx config and manifest
3. Run certbot for SSL, then `docker compose up -d`
4. Update BEEtexting webhook subscription to permanent URL (one final time)
5. Package and upload Teams manifest with permanent domain (one final time — never again)

---

## Remaining Engineering Work

1. **EC2 deploy** — test Docker locally, then deploy to `54.153.64.137` with permanent domain
2. Add automated smoke tests for key endpoints
3. Add health/readiness endpoint

---

## Hard Rules

- Never delete or mutate historical customer messages in external systems
- Integration is read + send only
- No destructive operations against BEEtexting/Teams/Zoho data
