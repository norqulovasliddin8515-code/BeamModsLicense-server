"""
verify.py — GET /verify : Ed25519-signed license check for the in-game controller.

Request (plain GET so LuaSocket's socket.http can call it):
    /verify?t=<token>&m=<mod_key>&h=<hwid sha512/256 hex>&n=<client nonce hex>&v=<client ver>

Response (text/plain, two lines):
    BMS1|<status>|<mod_key>|<token>|<hwid>|<nonce>|<issued_unix>|<expires_unix>
    <ed25519 signature hex of line 1>

status: ok | invalid | wrong_mod | revoked | hwid_mismatch
The client accepts ONLY a correctly signed line 1 whose mod_key/token/hwid/nonce
match its own values. A fake "hosts" server can't produce the signature, a
replayed old response fails the nonce check, and one mod's license can't
unlock a different mod.
"""
import logging
import os
import re
import time

from aiohttp import web

from bot.drm import licenses
from bot.drm.keys import get_signing_key

logger = logging.getLogger(__name__)

HWID_RE  = re.compile(r"^[0-9a-f]{64}$")
NONCE_RE = re.compile(r"^[0-9a-f]{32}$")
TICKET_TTL = int(os.getenv("DRM_TICKET_TTL_DAYS", "7")) * 86400   # offline grace period
MISMATCH_ALERT_EVERY = 3600                                       # max 1 admin alert/token/hour

_last_alert: dict[str, float] = {}


def sign_payload(status: str, mod_key: str, token: str, hwid: str, nonce: str) -> str:
    now = int(time.time())
    exp = now + TICKET_TTL if status == "ok" else now
    payload = f"BMS1|{status}|{mod_key}|{token}|{hwid}|{nonce}|{now}|{exp}"
    sig = get_signing_key().sign(payload.encode("ascii")).hex()
    return f"{payload}\n{sig}"


def make_verify_handler(bot=None, admin_id: int = 0):
    async def handle_verify(request: web.Request) -> web.Response:
        q = request.rel_url.query
        token, mod_key = q.get("t", ""), q.get("m", "")
        hwid, nonce = q.get("h", "").lower(), q.get("n", "").lower()
        ip = request.headers.get("X-Real-IP", request.remote or "")

        if not (licenses.TOKEN_RE.match(token) and licenses.MOD_KEY_RE.match(mod_key)
                and HWID_RE.match(hwid) and NONCE_RE.match(nonce)):
            return web.Response(status=400, text="bad request")

        try:
            status = await licenses.check_and_bind(token, mod_key, hwid, ip)
        except Exception as e:
            logger.error(f"[DRM] verify DB error: {e}")
            return web.Response(status=503, text="unavailable")   # client falls back to cache

        if status != "ok":
            logger.warning(f"[DRM] {status} token={token[:12]}… mod={mod_key} hwid={hwid[:12]}… ip={ip}")
        if status == "hwid_mismatch" and bot and admin_id:
            await _alert_admin(bot, admin_id, token, mod_key, hwid, ip)

        body = sign_payload(status, mod_key, token, hwid, nonce)
        return web.Response(
            text=body, content_type="text/plain",
            headers={"Cache-Control": "no-store"},
        )

    return handle_verify


async def _alert_admin(bot, admin_id: int, token: str, mod_key: str, hwid: str, ip: str) -> None:
    now = time.time()
    if now - _last_alert.get(token, 0) < MISMATCH_ALERT_EVERY:
        return
    _last_alert[token] = now
    lic = await licenses.get_license(token)
    if not lic:
        return
    try:
        await bot.send_message(
            admin_id,
            "🚨 <b>Possible leak</b>\n"
            f"Mod: <code>{mod_key}</code>\n"
            f"Buyer: <a href=\"tg://user?id={lic['user_id']}\">{lic['user_id']}</a>\n"
            f"Token: <code>{token}</code>\n"
            f"Bound HWID: <code>{(lic['hwid'] or '')[:16]}…</code>\n"
            f"New HWID: <code>{hwid[:16]}…</code>  IP: <code>{ip}</code>\n"
            f"Mismatches so far: {lic['mismatch_count']}\n\n"
            f"/license {token}\n/revoke {token}\n/resetlicense {token}",
        )
    except Exception as e:
        logger.warning(f"[DRM] admin alert failed: {e}")
