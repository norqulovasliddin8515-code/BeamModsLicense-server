"""
main.py — BeamModsStudio bot + API server birgalikda ishlaydi.

Ikkita jarayon parallel ishga tushadi:
  1. Aiogram 3.x bot polling  — Telegram bilan muloqot
  2. aiohttp web server       — Frontend uchun JSON API + To'lov webhooklar

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
from aiohttp import web

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.config import BOT_TOKEN, WEBHOOK_PORT, WEB_APP_URL
from bot import database as db
from bot.handlers import admin, user, ai_assistant, subscription
from bot.handlers import payment as pay_handler


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

ALLOWED_ORIGINS = {
    WEB_APP_URL,              # Vercel domeingiz
    "http://localhost:3000",  # Local development
    "http://127.0.0.1:5500",  # VS Code Live Server
    "https://footsore-ungraded-till.ngrok-free.dev",  # Ngrok tunnel
}


@web.middleware
async def cors_middleware(request: web.Request, handler):
    """
    Har bir HTTP javobga CORS sarlavhalarini qo'shadi.
    OPTIONS (preflight) so'rovlarini darhol 204 bilan javob beradi.
    """
    origin = request.headers.get("Origin", "")

    # Ruxsat etilgan origin yoki development uchun *
    allowed = origin if origin in ALLOWED_ORIGINS else "*"

    if request.method == "OPTIONS":
        # Preflight — brauzeri avval shu so'rovni yuboradi
        headers = {
            "Access-Control-Allow-Origin":  allowed,
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
            "Access-Control-Max-Age":       "3600",
        }
        return web.Response(status=204, headers=headers)

    response = await handler(request)
    response.headers["Access-Control-Allow-Origin"]  = allowed
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response


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
    app = web.Application(middlewares=[cors_middleware])

    # ── API Endpointlar ────────────────────────────────────────
    app.router.add_get("/health",         api_health)
    app.router.add_get("/api/mods",       api_get_mods)
    app.router.add_get("/api/mods/{id}",  api_get_mod)

    # OPTIONS (preflight) uchun ham route qo'shamiz
    app.router.add_options("/api/mods",      lambda r: web.Response(status=204))
    app.router.add_options("/api/mods/{id}", lambda r: web.Response(status=204))

    # ── To'lov Webhooklar ──────────────────────────────────────
    async def click_prepare(r):  return await pay_handler.click_prepare(r)
    async def click_complete(r): return await pay_handler.click_complete(r, bot)
    async def payme(r):          return await pay_handler.payme_webhook(r, bot)

    app.router.add_post("/payment/click/prepare",  click_prepare)
    app.router.add_post("/payment/click/complete", click_complete)
    app.router.add_post("/payment/payme",          payme)

    return app


# ═══════════════════════════════════════════════════════════════
#  Asosiy funksiya
# ═══════════════════════════════════════════════════════════════

async def main():
    # 1. Bazani ishga tushirish
    await db.init_db()

    # 2. Bot + Dispatcher
    bot = Bot(
        token   = BOT_TOKEN,
        default = DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    # 3. Handlerlar (tartib muhim — admin birinchi)
    dp.include_router(admin.router)
    dp.include_router(subscription.router)   # Obuna /plan /upgrade
    dp.include_router(user.router)
    dp.include_router(ai_assistant.router)

    # 4. aiohttp web server
    web_app = build_web_app(bot)
    runner  = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=WEBHOOK_PORT)
    await site.start()

    logger.info(f"API server: http://0.0.0.0:{WEBHOOK_PORT}/api/mods")
    logger.info(f"CORS allowed origins: {ALLOWED_ORIGINS}")

    # 5. Kunlik obuna tekshiruvi cron ni background task sifatida ishga tushirish
    asyncio.create_task(_subscription_expiry_cron(bot))
    logger.info("[Cron] Obuna expiry checker har 24 soatda ishlaydi.")

    # 6. Polling boshlash
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Bot polling started: @BeamModsStudio_bot")

    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
