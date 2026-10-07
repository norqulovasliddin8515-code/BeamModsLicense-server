"""
config.py — .env faylidan barcha sozlamalarni yuklaydi.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Telegram
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
ADMIN_ID: int = int(os.getenv("ADMIN_ID", "0"))
ARCHIVE_GROUP_ID: int = int(os.getenv("ARCHIVE_GROUP_ID", "0"))
WEB_APP_URL: str = os.getenv("WEB_APP_URL", "")

# OpenAI
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

# Click.uz
CLICK_SERVICE_ID: str = os.getenv("CLICK_SERVICE_ID", "")
CLICK_MERCHANT_ID: str = os.getenv("CLICK_MERCHANT_ID", "")
CLICK_SECRET_KEY: str = os.getenv("CLICK_SECRET_KEY", "")

# Payme
PAYME_MERCHANT_ID: str = os.getenv("PAYME_MERCHANT_ID", "")
PAYME_SECRET_KEY: str = os.getenv("PAYME_SECRET_KEY", "")

# ── HTTP server ───────────────────────────────────────────────
# Port the aiohttp server listens on. In production Nginx proxies to it.
WEBHOOK_PORT: int = int(os.getenv("WEBHOOK_PORT", "8080"))
# Production: 127.0.0.1 (only Nginx can reach it). Local dev: 0.0.0.0 is fine.
WEB_SERVER_HOST: str = os.getenv("WEB_SERVER_HOST", "127.0.0.1")

# ── Bot update mode ───────────────────────────────────────────
# "webhook" on the VPS, "polling" for local development on Windows.
RUN_MODE: str = os.getenv("RUN_MODE", "polling").strip().lower()

# Public HTTPS base URL of this backend, no trailing slash.
# Example: https://api.beammods.uz
PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

# Path Telegram POSTs updates to. Keep it unguessable.
WEBHOOK_PATH: str = os.getenv("WEBHOOK_PATH", "/tg/webhook")

# Sent by Telegram in the X-Telegram-Bot-Api-Secret-Token header.
# 1-256 chars, only A-Z a-z 0-9 _ -   Generate: python -c "import secrets;print(secrets.token_urlsafe(48))"
WEBHOOK_SECRET: str = os.getenv("WEBHOOK_SECRET", "")

# Extra allowed CORS origins, comma separated (WEB_APP_URL is always allowed).
EXTRA_CORS_ORIGINS: list[str] = [
    o.strip().rstrip("/") for o in os.getenv("EXTRA_CORS_ORIGINS", "").split(",") if o.strip()
]

# Max age of Mini App initData in seconds (default 24h).
INIT_DATA_MAX_AGE: int = int(os.getenv("INIT_DATA_MAX_AGE", str(24 * 3600)))

# ── DRM (Phase 2) ─────────────────────────────────────────────
# Ed25519 private key (PEM). Create: python -m bot.drm.keys generate <path>
DRM_SIGNING_KEY_FILE: str = os.getenv("DRM_SIGNING_KEY_FILE", "")
# Clean (unprotected) mod sources: <DRM_MODS_DIR>/<mod_key>/
DRM_MODS_DIR: str = os.getenv("DRM_MODS_DIR", "")

# ── Payments (Phase 3) ────────────────────────────────────────
# Telegram Stars: how many UZS one Star is worth (for price conversion).
# ~1 Star ≈ $0.013; tune to your Stars→USD→UZS rate. Example 170 => price_uzs/170 stars.
STARS_UZS_RATE: float = float(os.getenv("STARS_UZS_RATE", "170"))
# Payme sends Basic auth; we check the password equals PAYME_SECRET_KEY (set in cabinet).

# ── Manual card transfer (Phase 3, hybrid local payments) ─────
# Shown to UZ users who pay by card; they upload a receipt for admin approval.
CARD_NUMBER: str = os.getenv("CARD_NUMBER", "8600 0000 0000 0000")
CARD_HOLDER: str = os.getenv("CARD_HOLDER", "")
