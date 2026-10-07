"""
main.py — BeamModsStudio bot + API server birgalikda ishlaydi.

Bitta aiohttp server hammasini xizmat qiladi:
  1. Telegram updates — RUN_MODE=webhook (VPS) yoki RUN_MODE=polling (local dev)
  2. Frontend uchun JSON API + To'lov webhooklar

  POST {WEBHOOK_PATH}          — Telegram webhook (secret token bilan himoyalangan)

API Endpointlar:
  GET  /api/mods          — Barcha modlar (JSON)
  GET  /api/mods?category=cars — Kategoriya bo'yicha filter
  GET  /api/mods/{id}     — Bitta mod
  POST /payment/click/prepare  — Click.uz prepare
  POST /payment/click/complete — Click.uz complete
  POST /payment/payme          — Payme JSON-RPC

CORS:
  Vercel frontend domeniga (WEB_APP_URL) ruxsat beriladi.
  Development uchun "*" ishlatiladi.
"""
import asyncio
import json
import logging
import sys
import io
import re
import signal
from urllib.parse import urlparse
from aiohttp import web

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from bot.config import (
    BOT_TOKEN, WEBHOOK_PORT, WEB_APP_URL, WEB_SERVER_HOST,
    RUN_MODE, PUBLIC_BASE_URL, WEBHOOK_PATH, WEBHOOK_SECRET, EXTRA_CORS_ORIGINS,
)
from bot.webapp_auth import verify_init_data, InitDataError
from bot import database as db
from bot.handlers import admin, user, ai_assistant, subscription, drm_admin
from bot.config import STARS_UZS_RATE
from bot.drm.licenses import init_drm_tables
from bot.drm.verify import make_verify_handler
from bot.config import ADMIN_ID, DRM_SIGNING_KEY_FILE
from bot.payments import core as pay_core, click as pay_click, payme as pay_payme, stars as pay_stars, manual as pay_manual


# ─────────────────────────────────────────────────────────────────────
#  Kunlik cron: muddati o'tgan obunalarni tekshirish
# ─────────────────────────────────────────────────────────────────────

async def _subscription_expiry_cron(bot: Bot) -> None:
    """
    Har 24 soatda bir marta muddati o'tgan obunalarni 'free' ga tushiradi.

    Har foydalanuvchiga ogohlantirish xabari yuboriladi.
    Bot ishga tushganda darhol bir marta ishlaydi,
    keyin har 24 soatda takrorlaydi.
    """
    while True:
        try:
            expired_ids = await db.downgrade_expired_subscriptions()

            if expired_ids:
                logger.info(f"[Cron] {len(expired_ids)} ta foydalanuvchi obunasi muddati tugadi, free ga tushirildi.")
                # Har bir foydalanuvchiga ogohlantirish yuboramiz
                for uid in expired_ids:
                    try:
                        await bot.send_message(
                            uid,
                            "Obunangiz muddati tugadi va <b>Free</b> tarifga o'tdi.\n\n"
                            "Davom etish uchun /upgrade buyrug'ini bosing.",
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass  # Foydalanuvchi boti bloklagan bo'lishi mumkin
            else:
                logger.info("[Cron] Muddati tugagan obunalar topilmadi.")

        except Exception as e:
            logger.error(f"[Cron] Obuna tekshirishda xatolik: {e}")

        # Keyingi tekshirishgacha 24 soat kutamiz
        await asyncio.sleep(24 * 60 * 60)


# ── Windows da emoji chiqishini ta'minlash ────────────────────
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s | %(levelname)s | %(name)s - %(message)s",
    stream = sys.stdout,
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
#  CORS Middleware — Vercel frontend-ga ruxsat beradi
# ═══════════════════════════════════════════════════════════════

def _origin_of(url: str) -> str:
    """'https://x.vercel.app/some/path' -> 'https://x.vercel.app'"""
    u = urlparse(url)
    return f"{u.scheme}://{u.netloc}" if u.scheme and u.netloc else ""


ALLOWED_ORIGINS = {o for o in [_origin_of(WEB_APP_URL), *EXTRA_CORS_ORIGINS] if o}
if RUN_MODE != "webhook":
    # Local development only
    ALLOWED_ORIGINS |= {"http://localhost:3000", "http://127.0.0.1:5500"}


@web.middleware
async def cors_middleware(request: web.Request, handler):
    """
    Har bir HTTP javobga CORS sarlavhalarini qo'shadi.
    OPTIONS (preflight) so'rovlarini darhol 204 bilan javob beradi.
    """
    origin = request.headers.get("Origin", "")

    # Faqat ruxsat etilgan origin. Noma'lum origin -> CORS sarlavhasi yo'q
    # (brauzer javobni bloklaydi). Server-to-server (Telegram, Click, Payme)
    # so'rovlarida Origin bo'lmaydi va ularga CORS kerak emas.
    cors = {}
    if origin in ALLOWED_ORIGINS:
        cors = {
            "Access-Control-Allow-Origin":  origin,
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, ngrok-skip-browser-warning",
            "Vary":                         "Origin",
        }

    if request.method == "OPTIONS":
        return web.Response(status=204, headers={**cors, "Access-Control-Max-Age": "3600"})

    response = await handler(request)
    response.headers.update(cors)
    return response


def _json(payload: dict, status: int = 200) -> web.Response:
    return web.Response(
        text=json.dumps(payload, ensure_ascii=False),
        content_type="application/json",
        status=status,
    )


def _auth_user_id(body: dict) -> int:
    """Signed initData dan user_id. Body dagi user_id ga ISHONILMAYDI."""
    data = verify_init_data(body.get("init_data"))
    return int(data.user.id)


# ═══════════════════════════════════════════════════════════════
#  API Route Handlerlari
# ═══════════════════════════════════════════════════════════════

async def api_get_mods(request: web.Request) -> web.Response:
    """
    GET /api/mods
    GET /api/mods?category=cars

    Barcha modlarni yoki kategoriya bo'yicha filtrlangan modlarni
    JSON formatda qaytaradi.

    Javob formati:
    {
        "ok": true,
        "count": 5,
        "mods": [ { "id":1, "name":"BMW M5", ... }, ... ]
    }
    """
    category = request.rel_url.query.get("category")
    mods     = await db.get_all_mods(category=category)

    return web.Response(
        text         = json.dumps({"ok": True, "count": len(mods), "mods": mods},
                                  ensure_ascii=False),
        content_type = "application/json",
    )


async def api_get_mod(request: web.Request) -> web.Response:
    """
    GET /api/mods/{id}

    Bitta modni ID bo'yicha qaytaradi.
    """
    try:
        mod_id = int(request.match_info["id"])
    except (ValueError, KeyError):
        return web.Response(
            text         = json.dumps({"ok": False, "error": "Noto'g'ri ID"}),
            content_type = "application/json",
            status       = 400,
        )

    mod = await db.get_mod_by_id(mod_id)
    if not mod:
        return web.Response(
            text         = json.dumps({"ok": False, "error": "Mod topilmadi"}),
            content_type = "application/json",
            status       = 404,
        )

    return web.Response(
        text         = json.dumps({"ok": True, "mod": mod}, ensure_ascii=False),
        content_type = "application/json",
    )


async def api_health(request: web.Request) -> web.Response:
    """GET /health — Server tirik-tirikligini tekshiradi."""
    return web.Response(
        text         = json.dumps({"ok": True, "service": "BeamModsStudio API"}),
        content_type = "application/json",
    )


# ═══════════════════════════════════════════════════════════════
#  aiohttp App yaratish
# ═══════════════════════════════════════════════════════════════

def build_web_app(bot: Bot) -> web.Application:
    """
    aiohttp Application ni yaratadi, routerlarni qo'shadi va
    CORS middleware ni ulaydi.
    """
    app = web.Application(
        middlewares=[cors_middleware],
        client_max_size=1024 * 1024 * 20  # 20 MB limit
    )

    # ── API Endpointlar ────────────────────────────────────────
    app.router.add_get("/health",         api_health)
    app.router.add_get("/api/mods",       api_get_mods)
    app.router.add_get("/api/mods/{id}",  api_get_mod)

    # OPTIONS (preflight) uchun ham route qo'shamiz
    app.router.add_options("/api/mods",         lambda r: web.Response(status=204))
    app.router.add_options("/api/mods/{id}",    lambda r: web.Response(status=204))
    app.router.add_options("/api/download",     lambda r: web.Response(status=204))
    app.router.add_options("/api/upload_image", lambda r: web.Response(status=204))

    # ── Download Endpoint ──────────────────────────────────────
    # Mini App yopilmasin uchun sendData() o'rniga bu endpoint ishlatiladi.
    # Frontend: fetch('/api/download', {method:'POST', body:{mod_id, user_id}})
    async def api_download(request: web.Request) -> web.Response:
        """
        POST /api/download
        Body: { mod_id: int, init_data: str }   (init_data = Telegram.WebApp.initData)

        Bot faylni to'g'ridan user DM ga yuboradi (file_id orqali, yuklamaydi).
        Mini App yopilmaydi.
        """
        try:
            body    = await request.json()
            mod_id  = int(body.get("mod_id", 0))
        except Exception:
            return _json({"ok": False, "error": "mod_id va init_data kerak"}, 400)

        try:
            user_id = _auth_user_id(body)
        except InitDataError as e:
            logger.warning(f"[Download] auth rad etildi: {e} ip={request.headers.get("X-Real-IP", request.remote)}")
            return _json({"ok": False, "error": "unauthorized"}, 401)

        if not mod_id:
            return web.Response(
                text=json.dumps({"ok": False, "error": "Noto'g'ri parametrlar"}),
                content_type="application/json", status=400,
            )

        mod = await db.get_mod_by_id(mod_id)
        if not mod:
            return web.Response(
                text=json.dumps({"ok": False, "error": "Mod topilmadi"}),
                content_type="application/json", status=404,
            )

        # TODO (Phase 3): pullik modlar uchun to'lov/obuna tekshiruvi.
        # Hozircha har qanday tasdiqlangan foydalanuvchi har qanday modni oladi.

        # Demo mod tekshiruvi
        if not mod["file_id"] or mod["file_id"].startswith("demo_"):
            return web.Response(
                text=json.dumps({"ok": False, "error": "demo", "name": mod["name"]}),
                content_type="application/json", status=200,
            )

        # Faylni Telegram orqali yuborish
        try:
            price_str = f"{mod['price']:,}".replace(",", " ") + " UZS" if mod["price"] else "Bepul"
            await bot.send_document(
                chat_id   = user_id,
                document  = mod["file_id"],
                caption   = (
                    f"<b>{mod['name']}</b>\n"
                    f"Kategoriya: {mod['category']}   |   {price_str}\n\n"
                    f"{mod.get('description', '')}"
                ),
                parse_mode = "HTML",
            )
            logger.info(f"[Download] user={user_id} mod={mod_id} ({mod['name']})")
            return web.Response(
                text=json.dumps({"ok": True, "name": mod["name"]}),
                content_type="application/json",
            )
        except Exception as e:
            logger.error(f"[Download] Xatolik user={user_id} mod={mod_id}: {e}")
            return _json({"ok": False, "error": "send_failed"}, 500)

    app.router.add_post("/api/download", api_download)

    # ── Photo Proxy (Telegram photo file_id → image bytes) ──
    # OLDIN: 302 redirect https://api.telegram.org/file/bot<TOKEN>/... edi —
    # bu BOT_TOKEN ni har bir brauzerga oshkor qilardi. Endi rasm baytlarini
    # o'zimiz uzatamiz; token serverdan chiqmaydi. Nginx bu javobni keshlaydi.
    _FILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,256}$")

    async def api_photo(request: web.Request) -> web.Response:
        """GET /api/photo/{file_id} — Mini App uchun mod rasmlari."""
        file_id = request.match_info.get("file_id", "")
        if not _FILE_ID_RE.match(file_id):
            return web.Response(status=400, text="bad file_id")

        try:
            tg_file = await bot.get_file(file_id)
            if not tg_file.file_path:
                return web.Response(status=404, text="Photo not found")
            buf = await bot.download_file(tg_file.file_path)
            return web.Response(
                body=buf.read(),
                content_type="image/jpeg",
                headers={"Cache-Control": "public, max-age=86400, immutable"},
            )
        except Exception as e:
            logger.warning(f"[Photo] {file_id[:16]}…: {e}")
            return web.Response(status=404, text="Photo not found")

    app.router.add_get("/api/photo/{file_id}", api_photo)

    # 🔹 Image Upload (Admin panel - WebApp) 🔹
    async def api_upload_image(request: web.Request) -> web.Response:
        import base64
        import json
        from aiogram.types import BufferedInputFile
        from bot.config import ADMIN_ID, ARCHIVE_GROUP_ID
        from bot.database import DB_PATH
        from bot.handlers.admin import sync_mods_to_github
        import aiosqlite

        try:
            body = await request.json()
            mod_id = int(body.get("mod_id", 0))
            base64_img = body.get("image_base64", "")
        except Exception:
            return web.Response(text=json.dumps({"ok": False, "error": "Invalid JSON"}), content_type="application/json", status=400)

        try:
            user_id = _auth_user_id(body)
        except InitDataError as e:
            logger.warning(f"[UploadImage] auth rad etildi: {e} ip={request.headers.get("X-Real-IP", request.remote)}")
            return _json({"ok": False, "error": "unauthorized"}, 401)

        if user_id != ADMIN_ID:
            return web.Response(text=json.dumps({"ok": False, "error": "Faqat admin o'zgartira oladi!"}), content_type="application/json", status=403)
        if not mod_id or not base64_img:
            return web.Response(text=json.dumps({"ok": False, "error": "mod_id va image_base64 kerak"}), content_type="application/json", status=400)

        try:
            header, encoded = base64_img.split(",", 1) if "," in base64_img else ("", base64_img)
            img_data = base64.b64decode(encoded)

            msg = await bot.send_photo(
                chat_id=ARCHIVE_GROUP_ID,
                photo=BufferedInputFile(img_data, filename="mod_cover.jpg"),
                caption=f"📝 Mod ID: {mod_id} rasmi WebApp orqali yangilandi."
            )
            file_id = msg.photo[-1].file_id
            image_url = f"tg_photo:{file_id}"

            async with aiosqlite.connect(DB_PATH) as db_conn:
                await db_conn.execute("UPDATE mods SET image_url=? WHERE id=?", (image_url, mod_id))
                await db_conn.commit()

            asyncio.create_task(sync_mods_to_github())

            return web.Response(text=json.dumps({"ok": True, "file_id": file_id}), content_type="application/json")
        except Exception as e:
            logger.error(f"[UploadImage] Error: {e}")
            return _json({"ok": False, "error": "upload_failed"}, 500)

    app.router.add_post("/api/upload_image", api_upload_image)

    # ── To'lov Webhooklar (Click / Payme) ──────────────────────
    app.router.add_post("/payment/click/prepare",  pay_click.prepare)
    app.router.add_post("/payment/click/complete", pay_click.make_complete_handler(bot))
    app.router.add_post("/payment/payme",          pay_payme.make_payme_handler(bot))

    # ── Mini App: to'lovni boshlash (signed initData) ──────────
    def _make_pay_start_handler():
        async def api_pay_start(request: web.Request) -> web.Response:
            try:
                body = await request.json()
                intent   = str(body.get("intent", ""))
                provider = str(body.get("provider", ""))
            except Exception:
                return _json({"ok": False, "error": "bad_request"}, 400)
            try:
                data = verify_init_data(body.get("init_data"))
                user_id = int(data.user.id)
            except InitDataError:
                return _json({"ok": False, "error": "unauthorized"}, 401)
            if not pay_core.INTENT_RE.match(intent) or provider not in ("stars", "card"):
                return _json({"ok": False, "error": "bad_params"}, 400)
            try:
                amount_uzs = await pay_core.price_for_intent(intent)
            except Exception:
                return _json({"ok": False, "error": "unknown_item"}, 404)
            if provider == "stars":
                await pay_stars.send_stars_invoice(bot, user_id, intent, "BeamModsStudio", "BeamModsStudio")
                return _json({"ok": True, "provider": "stars", "sent_in_chat": True})
            if provider == "card":
                payment = await pay_manual.start_card_payment(bot, user_id, intent)
                return _json({"ok": True, "provider": "card", "payment_id": payment.id,
                              "amount_uzs": amount_uzs, "sent_in_chat": True})
            return _json({"ok": False, "error": "bad_params"}, 400)
        return api_pay_start

    app.router.add_post("/api/pay/start", _make_pay_start_handler())
    app.router.add_options("/api/pay/start", lambda r: web.Response(status=204))

    # ── DRM: Ed25519-signed license check (BeamNG controller) ──
    if DRM_SIGNING_KEY_FILE:
        from bot.drm.keys import get_public_hex
        logger.info(f"[DRM] /verify enabled, public key {get_public_hex()}")   # fails fast if key is bad
        app.router.add_get("/verify", make_verify_handler(bot, ADMIN_ID))
    else:
        logger.warning("[DRM] DRM_SIGNING_KEY_FILE not set — /verify disabled")

    return app


# ═══════════════════════════════════════════════════════════════
#  Asosiy funksiya
# ═══════════════════════════════════════════════════════════════

def _build_bot() -> Bot:
    import socket
    from aiogram.client.session.aiohttp import AiohttpSession

    session = AiohttpSession()
    # IPv4 majburiy (WinError 121 fix). Ba'zi VPS larda IPv6 ham buzuq bo'ladi.
    session._connector_init["family"] = socket.AF_INET

    return Bot(
        token   = BOT_TOKEN,
        default = DefaultBotProperties(parse_mode=ParseMode.HTML),
        session = session,
    )


def _build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    # Handlerlar (tartib muhim — admin birinchi)
    dp.include_router(admin.router)
    dp.include_router(subscription.router)   # Obuna /plan /upgrade
    dp.include_router(user.router)
    dp.include_router(drm_admin.router)      # /deliver /license /revoke /resetlicense
    dp.include_router(pay_stars.router)      # Telegram Stars (XTR)
    dp.include_router(pay_manual.router)     # Card transfer + admin approve/reject
    dp.include_router(ai_assistant.router)
    return dp


async def _wait_for_shutdown_signal() -> None:
    """systemd SIGTERM yuborganda toza to'xtash uchun."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:   # Windows
            pass
    await stop.wait()


async def main():
    # 1. Bazani ishga tushirish
    await db.init_db()
    await init_drm_tables()
    await pay_core.init_payment_tables()

    # 2. Bot + Dispatcher
    bot = _build_bot()
    dp  = _build_dispatcher()

    # 3. aiohttp web app (API + to'lovlar)
    web_app = build_web_app(bot)

    # 4. Kunlik obuna tekshiruvi
    cron_task = asyncio.create_task(_subscription_expiry_cron(bot))
    logger.info("[Cron] Obuna expiry checker har 24 soatda ishlaydi.")
    logger.info(f"CORS allowed origins: {ALLOWED_ORIGINS}")

    if RUN_MODE == "webhook":
        await _run_webhook(bot, dp, web_app)
    else:
        await _run_polling(bot, dp, web_app)

    cron_task.cancel()


async def _run_webhook(bot: Bot, dp: Dispatcher, web_app: web.Application) -> None:
    if not PUBLIC_BASE_URL.startswith("https://"):
        raise RuntimeError("RUN_MODE=webhook uchun PUBLIC_BASE_URL https:// bilan boshlanishi kerak")
    if not WEBHOOK_SECRET or not re.fullmatch(r"[A-Za-z0-9_-]{16,256}", WEBHOOK_SECRET):
        raise RuntimeError("WEBHOOK_SECRET kerak: 16-256 belgi, faqat A-Z a-z 0-9 _ -")

    webhook_url = f"{PUBLIC_BASE_URL}{WEBHOOK_PATH}"

    async def on_startup(bot: Bot) -> None:
        await bot.set_webhook(
            url                  = webhook_url,
            secret_token         = WEBHOOK_SECRET,
            allowed_updates      = dp.resolve_used_update_types(),
            drop_pending_updates = False,   # restart paytida kelgan xabarlar yo'qolmaydi
            max_connections      = 40,
        )
        logger.info(f"Webhook o'rnatildi: {PUBLIC_BASE_URL}{WEBHOOK_PATH[:4]}…")

    dp.startup.register(on_startup)
    # Eslatma: shutdown da delete_webhook QILMAYMIZ — restart vaqtida Telegram
    # update larni navbatda saqlab turadi va qayta yuboradi.

    SimpleRequestHandler(
        dispatcher   = dp,
        bot          = bot,
        secret_token = WEBHOOK_SECRET,   # X-Telegram-Bot-Api-Secret-Token tekshiriladi
    ).register(web_app, path=WEBHOOK_PATH)
    setup_application(web_app, dp, bot=bot)   # dp startup/shutdown + bot.session.close

    runner = web.AppRunner(web_app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host=WEB_SERVER_HOST, port=WEBHOOK_PORT)
    await site.start()
    logger.info(f"Webhook server: http://{WEB_SERVER_HOST}:{WEBHOOK_PORT} (Nginx orqali)")

    try:
        await _wait_for_shutdown_signal()
    finally:
        logger.info("To'xtatilmoqda...")
        await runner.cleanup()


async def _run_polling(bot: Bot, dp: Dispatcher, web_app: web.Application) -> None:
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, host=WEB_SERVER_HOST, port=WEBHOOK_PORT)
    await site.start()
    logger.info(f"API server: http://{WEB_SERVER_HOST}:{WEBHOOK_PORT}/api/mods")

    # DIQQAT: bu production webhookni o'chiradi! Local dev uchun alohida test bot token ishlating.
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Bot polling started")
    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
