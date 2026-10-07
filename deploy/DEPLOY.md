# Phase 1 — Deploy BeamModsStudio to a Linux VPS (Nginx + HTTPS + Webhook)

Target layout on the server:

```
Internet ──443──► Nginx (TLS, rate limits, Telegram IP allowlist, photo cache)
                    │
                    └──► 127.0.0.1:8080  aiohttp (python -m bot.main, RUN_MODE=webhook)
                           ├─ /tg/<secret-path>   Telegram updates (secret header checked)
                           ├─ /api/*              Mini App API (initData-signed)
                           └─ /payment/*          Click / Payme callbacks
/opt/beammods/
  ├─ app/        git clone of this repo  (+ .env, beamods.db)
  ├─ venv/       Python virtualenv
  ├─ backups/    nightly SQLite backups (14 days)
  └─ .ssh/       GitHub deploy key (bot pushes mods.json)
```

---

## Step 0 — URGENT: revoke the leaked bot token (do this first, today)

The current token was hardcoded in `index.html` (served publicly by Vercel) and in
`api/index.py` (in the GitHub repo). Anyone holding it can read your bot's updates,
send messages as the bot, and send your paid mod files to anyone.

1. Telegram → @BotFather → `/revoke` → choose @BeamModsStudio_bot → copy the new token.
2. Put the new token into your local `.env` (`BOT_TOKEN=`).
3. Vercel → Project → Settings → Environment Variables → delete `BOT_TOKEN` (no longer needed).
4. The old token stays in git history, but once it's revoked it's useless.

> File IDs (`file_id`) are tied to the bot, not the token, so all your uploaded mods keep working.

## Step 1 — Create a test bot for local development

`RUN_MODE=polling` calls `delete_webhook()`. If you run polling on Windows with the
**production** token, you silently switch production off.

- @BotFather → `/newbot` → e.g. `BeamModsStudioDevBot`.
- Local `.env` on Windows uses the **dev** token + `RUN_MODE=polling`.
- Only the VPS `.env` contains the production token.

## Step 2 — VPS + domain

- VPS: Ubuntu **24.04 LTS**, 1 vCPU / 1–2 GB RAM is plenty for now. A European location
  (e.g. Frankfurt/Amsterdam) gives low latency to Telegram's servers.
- Domain: any registrar. Create a DNS record:

  | Type | Name  | Value         |
  |------|-------|---------------|
  | A    | `api` | `<VPS IPv4>`  |
  | AAAA | `api` | `<VPS IPv6>` (only if the VPS has IPv6) |

- If you use Cloudflare: set the `api` record to **DNS only (grey cloud)**. With the
  orange-cloud proxy, Nginx would see Cloudflare IPs and the Telegram IP allowlist would
  block real webhooks.
- Check that the record is visible from the internet: `nslookup api.yourdomain.uz` should return the VPS IP.

## Step 3 — Push the updated code to GitHub (from Windows)

```powershell
cd C:\beammodsstudio
git add -A
git commit -m "Phase 1: webhook mode, initData auth, deploy files"
git push origin main
```

Recommended: make the GitHub repo **private** now (Settings → General → Danger zone).
The frontend no longer reads `raw.githubusercontent.com`, and Vercel can still deploy from a private repo.

## Step 4 — First login and server bootstrap

```bash
ssh root@<VPS_IP>

# get the setup script from your repo (or paste it via nano)
apt-get update && apt-get install -y git
git clone https://github.com/<you>/<repo>.git /tmp/bm   # use a token if the repo is private
sudo DOMAIN=api.yourdomain.uz \
     EMAIL=you@gmail.com \
     REPO=git@github.com:<you>/<repo>.git \
     bash /tmp/bm/deploy/setup_server.sh
```

The script:
1. installs Python, Nginx, Certbot, UFW (ports 22/80/443 only), fail2ban and unattended-upgrades
2. creates the unprivileged `beammods` user with home `/opt/beammods`
3. generates an SSH **deploy key** and pauses. Add it in GitHub → repo → Settings → Deploy keys,
   tick **Allow write access** (the bot's `sync_mods_to_github()` pushes `mods.json`), then press Enter
4. clones the repo, builds the venv, gets a Let's Encrypt certificate (auto-renews), and installs the
   Nginx config and systemd unit
5. sets up a nightly SQLite backup at 03:30

## Step 5 — Copy secrets and the database (from Windows PowerShell)

```powershell
scp C:\beammodsstudio\beamods.db root@<VPS_IP>:/opt/beammods/app/beamods.db
scp C:\beammodsstudio\deploy\.env.production.example root@<VPS_IP>:/opt/beammods/app/.env
```

On the server:

```bash
cd /opt/beammods/app
python3 -c "import secrets;print('/tg/'+secrets.token_urlsafe(32))"   # -> WEBHOOK_PATH
python3 -c "import secrets;print(secrets.token_urlsafe(48))"          # -> WEBHOOK_SECRET
nano .env            # fill BOT_TOKEN (new prod token), PUBLIC_BASE_URL, WEBHOOK_PATH, WEBHOOK_SECRET, the rest
chown beammods:beammods .env beamods.db
chmod 600 .env
chmod 640 beamods.db
```

> Copy `beamods.db` (in the project root, ~28 KB). That's the one the bot uses (`DB_PATH = "beamods.db"`).
> `bot/database.db` is empty.

## Step 6 — Start and verify

```bash
systemctl start beammods
journalctl -u beammods -f            # expect: "Webhook o'rnatildi" and "[OK] Database ready."

curl -s https://api.yourdomain.uz/health
curl -s "https://api.telegram.org/bot<NEW_TOKEN>/getWebhookInfo" | python3 -m json.tool
#   url                    -> https://api.yourdomain.uz/tg/...
#   pending_update_count   -> 0
#   last_error_message     -> absent
```

Send `/start` to the bot and check that it answers.

## Step 7 — Point the Mini App at the new backend

In `index.html` **and** `webapp/index.html` (they're identical):

```js
const API_URL = 'https://api.yourdomain.uz';
```

Commit and push. Vercel redeploys. Then open the Mini App from Telegram and:
- check that the catalog and cover images load
- tap a free mod and check that the file arrives in the bot chat
- as admin, change a cover image and check that it saves

> Downloads and admin uploads now send `Telegram.WebApp.initData`. The server verifies Telegram's
> signature and takes the user ID from it. A `user_id` typed into a request body is ignored.
> These requests only work when the Mini App is opened from inside Telegram.

## Step 8 — Harden SSH (after you confirm key login works)

```bash
# on Windows: ssh-keygen -t ed25519 ; then copy the .pub content to the server:
mkdir -p ~/.ssh && nano ~/.ssh/authorized_keys      # paste your public key
# open a SECOND terminal and confirm `ssh root@IP` works without a password, then:
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl restart ssh
```

---

## Day-to-day operations

| Task | Command |
|---|---|
| Deploy new code | `sudo bash /opt/beammods/app/deploy/update.sh` |
| Live logs | `journalctl -u beammods -f` |
| Restart | `systemctl restart beammods` |
| Nginx errors | `tail -f /var/log/nginx/beammods.error.log` |
| Webhook status | `curl -s https://api.telegram.org/bot$TOKEN/getWebhookInfo` |
| Cert renewal test | `certbot renew --dry-run` |
| Manual DB backup | `sqlite3 /opt/beammods/app/beamods.db ".backup /opt/beammods/backups/manual.db"` |

**Rollback to local polling** (emergency): on Windows put the production token in `.env`, set
`RUN_MODE=polling` and run `python -m bot.main`. That deletes the webhook automatically.
Stop the VPS service first (`systemctl stop beammods`) so two instances aren't running at once.

**Run one instance only.** SQLite, `MemoryStorage` (FSM) and the in-process expiry cron all
assume a single process. Don't add `--workers` or a second server until Phase 4 (PostgreSQL + Redis FSM).

## What changed in the code

| File | Change |
|---|---|
| `bot/main.py` | `RUN_MODE=webhook` uses aiogram `SimpleRequestHandler` with a secret token, `set_webhook` on startup and clean SIGTERM shutdown. Polling is kept for dev. `/api/photo` now streams the image instead of redirecting to a URL with the bot token in it. `/api/download` and `/api/upload_image` take the user from signed initData. CORS is a strict allowlist (no `*` fallback). |
| `bot/webapp_auth.py` | New. Telegram initData HMAC verification plus a 24 h freshness check. |
| `bot/config.py` | New settings: `RUN_MODE`, `PUBLIC_BASE_URL`, `WEBHOOK_PATH`, `WEBHOOK_SECRET`, `WEB_SERVER_HOST`, `EXTRA_CORS_ORIGINS`, `INIT_DATA_MAX_AGE`. |
| `bot/requirements.txt` | Pins `httpx==0.27.2`, because `openai==1.40.0` crashes on startup with httpx ≥ 0.28 (fresh installs). |
| `index.html`, `webapp/index.html` | Hardcoded bot token removed. All calls go to `API_URL` with `init_data`. The admin image upload goes through the backend. |
| `api/index.py`, `vercel.json` | The Vercel API function is retired (410). It had the token and let anyone deliver any mod to any user ID. |
| `deploy/` | Nginx config, systemd unit, bootstrap and update scripts, production `.env` template. |
| `.gitattributes` | Keeps LF endings on `.sh`/`.conf`/`.service`. CRLF breaks bash on Linux. |

## Known open items (later phases)

- **Paywall on `/api/download`** (Phase 3): any authenticated user can still download any non-demo
  mod. There is a `TODO` at that spot in `main.py`. We'll gate it on `has_purchased()` or the subscription tier.
- **Payment callback IP allowlists** for Click/Payme in Nginx (Phase 3).
- **`/verify` DRM endpoint**: `bundler.py` / `drm_controller.lua` / the `licenses` table aren't in this
  folder. Nginx returns 404 for unknown paths, so the endpoint needs a `location` block when we add it in Phase 2.
