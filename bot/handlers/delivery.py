"""
delivery.py — To'lov muvaffaqiyatidan so'ng faylni avtomatik yuborish.

Asosiy mantiq:
  - Bot file_id ni bazadan olib, uni YUKLAB OLMAYDI.
  - bot.send_document(file_id=...) — Telegram serverida allaqachon saqlangan
    faylni to'g'ridan-to'g'ri foydalanuvchiga yo'naltiradi (bandwidth = 0).
  - Bu yondashuv 2GB gacha bo'lgan fayllar uchun ham ishlaydi.
"""
from aiogram import Bot
from aiogram.types import FSInputFile

from bot import database as db


async def send_file_to_user(bot: Bot, user_id: int, mod_id: int) -> bool:
    """
    Muvaffaqiyatli to'lovdan so'ng faylni foydalanuvchi DM ga yuboradi.

    Returns:
        True — fayl muvaffaqiyatli yuborildi
        False — xato yuz berdi
    """
    mod = await db.get_mod_by_id(mod_id)
    if not mod:
        await bot.send_message(
            chat_id = user_id,
            text    = "❗ Kechirasiz, mod bazada topilmadi. Admin bilan bog'laning.",
        )
        return False

    file_id  = mod["file_id"]
    mod_name = mod["name"]

    try:
        # ──────────────────────────────────────────────────────────────────────
        # MUHIM: file_id orqali yuborish — hech qanday yuklab olish yo'q!
        # Telegram serveri faylni to'g'ridan-to'g'ri foydalanuvchiga jo'natadi.
        # Bu 2GB gacha bo'lgan fayllar uchun ham bir zumda ishlaydi.
        # ──────────────────────────────────────────────────────────────────────
        await bot.send_document(
            chat_id  = user_id,
            document = file_id,
            caption  = (
                f"✅ <b>Xarid muvaffaqiyatli!</b>\n\n"
                f"📦 Mod: <b>{mod_name}</b>\n\n"
                f"📥 Faylni saqlang va BeamNG.drive ga o'rnating.\n"
                f"❓ Yordam kerak bo'lsa: /help"
            ),
            parse_mode = "HTML",
        )

        # Yuklash sonini yangilash (ixtiyoriy statistika)
        await _increment_downloads(mod_id)
        return True

    except Exception as e:
        # Agar bot foydalanuvchi bilan suhbat boshlamamagan bo'lsa
        print(f"Fayl yuborishda xato (user={user_id}, mod={mod_id}): {e}")
        await bot.send_message(
            chat_id = user_id,
            text    = (
                f"❗ Fayl yuborishda texnik xato yuz berdi.\n"
                f"Iltimos admin bilan bog'laning va buyurtma ID sini yuboring.\n\n"
                f"📦 Mod: {mod_name}"
            ),
        )
        return False


async def resend_file(bot: Bot, user_id: int, mod_id: int) -> None:
    """
    Foydalanuvchi /myfiles buyrug'i berganda allaqachon sotib olingan
    faylni qayta yuboradi.
    """
    already_bought = await db.has_user_purchased(user_id, mod_id)
    if not already_bought:
        await bot.send_message(
            chat_id = user_id,
            text    = "❗ Siz bu modni sotib olmadingiz.",
        )
        return

    await send_file_to_user(bot, user_id, mod_id)


async def _increment_downloads(mod_id: int) -> None:
    """Modni yuklab olishlar sonini 1 ga oshiradi."""
    import aiosqlite
    from bot.database import DB_PATH

    async with aiosqlite.connect(DB_PATH) as db_conn:
        await db_conn.execute(
            "UPDATE mods SET downloads = downloads + 1 WHERE id = ?",
            (mod_id,),
        )
        await db_conn.commit()
