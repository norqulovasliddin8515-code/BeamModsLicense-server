"""
main.py — BeamModsStudio botning asosiy entry point faylidir.

Quyidagilarni ishga tushiradi:
  1. SQLite baza init
  2. Aiogram 3.x Dispatcher
  3. Barcha handlerlarni ro'yxatdan o'tkazish
  4. aiohttp orqali Click/Payme webhook server
  5. Bot polling (yoki webhook) ni boshlash
"""
import asyncio
import logging
from aiohttp import web

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.config import BOT_TOKEN, WEBHOOK_PORT
from bot import database as db
from bot.handlers import admin, user, ai_assistant
from bot.handlers import payment


# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s | %(levelname)s | %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


# ── aiohttp webhook routes ─────────────────────────────────────────────────────

def create_aiohttp_app(bot: Bot) -> web.Application:
    """Click va Payme uchun webhook web serverni yaratadi."""
    app = web.Application()

    # Click.uz webhooks
    async def click_prepare_handler(request: web.Request):
        return await payment.click_prepare(request)

    async def click_complete_handler(request: web.Request):
        return await payment.click_complete(request, bot)

    # Payme webhook
    async def payme_handler(request: web.Request):
        return await payment.payme_webhook(request, bot)

    app.router.add_post("/payment/click/prepare",  click_prepare_handler)
    app.router.add_post("/payment/click/complete", click_complete_handler)
    app.router.add_post("/payment/payme",          payme_handler)

    return app


# ── Asosiy funksiya ────────────────────────────────────────────────────────────

async def main():
    # 1. Ma'lumotlar bazasini ishga tushirish
    await db.init_db()

    # 2. Bot va Dispatcher
    bot = Bot(
        token   = BOT_TOKEN,
        default = DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp  = Dispatcher(storage=MemoryStorage())

    # 3. Handlerlarni ulash (tartib muhim — admin birinchi!)
    dp.include_router(admin.router)
    dp.include_router(user.router)
    dp.include_router(ai_assistant.router)

    # 4. aiohttp webhook serverini alohida taskda ishga tushirish
    aiohttp_app = create_aiohttp_app(bot)
    runner      = web.AppRunner(aiohttp_app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=WEBHOOK_PORT)
    await site.start()
    logger.info(f"💳 To'lov webhook server: http://0.0.0.0:{WEBHOOK_PORT}")

    # 5. Kutilmagan updatelarni o'chirish va polling ni boshlash
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("🤖 BeamModsStudio Bot ishga tushdi!")

    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
