# BEEtexting ↔ Microsoft Teams ↔ Zoho Desk Bridge

Microservice that bridges customer SMS (BEEtexting) into Microsoft Teams and lets agents reply, with Zoho Desk ticket creation.

## Architecture

```
EC2 Instance
├── nginx (ports 80/443) — SSL termination, reverse proxy
├── app (port 8000, internal) — FastAPI bridge service
└── certbot — auto-renews Let's Encrypt SSL cert
```

All three run as Docker containers managed by docker-compose.

---

## One-Time EC2 Setup

### 1. Launch EC2 Instance

- AMI: **Ubuntu 22.04 LTS**
- Type: `t3.micro` (or larger)
- Security group inbound rules:
  - Port 22 (SSH)
  - Port 80 (HTTP)
  - Port 443 (HTTPS)

### 2. Install Docker

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y docker.io docker-compose-plugin
sudo usermod -aG docker ubuntu
# Log out and back in for group to take effect
```

### 3. Point Your Domain at EC2

In your DNS provider, create an A record:
```
bridge.yourdomain.com → <EC2 public IP>
```

Wait for DNS to propagate before proceeding.

### 4. Clone/Copy This Repo

```bash
git clone <your-repo-url> bridge
cd bridge
```

### 5. Configure Environment

```bash
cp .env.example .env
nano .env
```

Fill in all values. Set `PUBLIC_BASE_URL` and `TASK_MODULE_BASE_URL` to your permanent domain:
```env
PUBLIC_BASE_URL=https://bridge.yourdomain.com
TASK_MODULE_BASE_URL=https://bridge.yourdomain.com
```

### 6. Update Nginx Config

```bash
nano nginx/default.conf
```

Replace all instances of `YOUR_DOMAIN` with your actual domain (e.g. `bridge.yourdomain.com`).

### 7. Get SSL Certificate

Start nginx on HTTP only first (comment out the HTTPS server block temporarily), then:

```bash
docker compose up -d nginx
docker compose run --rm certbot certonly --webroot \
    --webroot-path=/var/www/certbot \
    --email your@email.com \
    --agree-tos --no-eff-email \
    -d bridge.yourdomain.com
```

Uncomment the HTTPS server block in `nginx/default.conf`, then restart nginx:
```bash
docker compose restart nginx
```

### 8. Start Everything

```bash
docker compose up -d
```

Verify:
```bash
curl https://bridge.yourdomain.com/webhook
# Should return: {"status":"ok"}
```

### 9. Set Up BEEtexting Webhook Subscription

```bash
# First, get a user refresh token (one-time, opens browser via SSH tunnel or locally)
python src/beetexting_user_auth.py

# Then create the webhook subscription
python src/beetexting_subscribe.py --create \
    --webhook-url "https://bridge.yourdomain.com/webhook"
```

### 10. Update Teams App Manifest

Edit `teams_manifest/manifest.json`:
- Replace tunnel URL with your permanent domain everywhere
- Bump version to `1.1.0`

Repackage:
```bash
cd teams_manifest
zip ../rc-zd-teams-bridge-v1.1.0.zip manifest.json color.png outline.png
cd ..
```

Upload `rc-zd-teams-bridge-v1.1.0.zip` to Teams Admin Center → Manage apps → rc-zd-teams-bridge → Upload file.

Also update Azure Bot messaging endpoint to:
```
https://bridge.yourdomain.com/api/messages
```

**This is the last time you ever need to update these URLs.**

---

## Daily Operations

### View logs
```bash
docker compose logs -f app
```

### Restart app
```bash
docker compose restart app
```

### Deploy updated code
```bash
git pull
docker compose build app
docker compose up -d app
```

### SSL cert renewal
Certbot renews automatically every 12 hours. To force renewal:
```bash
docker compose run --rm certbot renew
docker compose restart nginx
```

---

## Agent Usage in Teams

Agents must **@mention** the bot when replying:
```
@rc-zd-teams-bridge Your reply here
```

The mention is stripped before the SMS is sent to the customer.

---

## Key Files

| File | Purpose |
|------|---------|
| `src/beetexting_webhook.py` | FastAPI app — all HTTP endpoints |
| `src/teams_bot.py` | Teams activity handler |
| `src/teams_client.py` | Proactive Teams thread management |
| `src/message_store.py` | SQLite persistence |
| `src/zoho_desk.py` | Zoho Desk ticket creation |
| `src/beetexting_subscribe.py` | Manage BEEtexting webhook subscriptions |
| `static/create_ticket.html` | Ticket creation modal UI |
| `teams_manifest/` | Teams app package source files |
| `nginx/default.conf` | Nginx reverse proxy config |

---

## Hard Rules

- Never delete or mutate historical customer messages
- Integration is read + send only
- No destructive operations against BEEtexting/Teams/Zoho data
