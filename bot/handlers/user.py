"""
user.py — Foydalanuvchi uchun asosiy handlerlar:
  /start — Salomlashuv + Web App tugmasi
  /catalog — Katalog (inline buttons)
  /myorders — Sotib olingan modlar ro'yxati
  web_app_data — TWA dan kelgan JSON ma'lumotlarni qayta ishlash
"""
import json
from aiogram import Router, F, types
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    WebAppInfo,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)

from bot.config import ADMIN_ID, WEB_APP_URL
from bot import database as db

router = Router()


# ── /start buyrug'i ────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: types.Message):
    """Foydalanuvchini ro'yxatdan o'tkazadi va Web App tugmasini yuboradi."""
    user = message.from_user

    # Bazaga qo'shish / yangilash
    await db.upsert_user(
        user_id  = user.id,
        name     = user.full_name,
        username = user.username,
    )


    # Web App tugmasi
    web_app_btn = KeyboardButton(
        text    = "🏪 BeamModsStudio ni ochish",
        web_app = WebAppInfo(url=WEB_APP_URL),
    )
    markup = ReplyKeyboardMarkup(
        keyboard         = [[web_app_btn]],
        resize_keyboard  = True,
    )

    await message.answer(
        f"🎮 Xush kelibsiz, <b>{user.first_name}</b>!\n\n"
        f"<b>BeamModsStudio</b> — BeamNG.drive uchun premium modlar, "
        f"xaritalar va 3D modellar do'koni.\n\n"
        f"👇 Katalogni ochish uchun tugmani bosing:",
        parse_mode  = "HTML",
        reply_markup = markup,
    )


# ── /myorders buyrug'i ─────────────────────────────────────────────────────────

@router.message(Command("myorders"))
async def cmd_my_orders(message: types.Message):
    """Foydalanuvchining sotib olingan modlarini ko'rsatadi."""
    orders = await db.get_user_purchases(message.from_user.id)

    if not orders:
        await message.answer(
            "🛒 Siz hali hech qanday mod sotib olmadingiz.\n"
            "Katalogni ko'rish uchun /start bosing."
        )
        return

    lines = [f"📦 <b>Sizning xaridlaringiz ({len(orders)} ta):</b>\n"]
    for o in orders:
        price_fmt = f"{o['amount']:,}".replace(",", " ")
        lines.append(f"✅ {o['name']} — {price_fmt} UZS ({o['paid_at'][:10]})")

    await message.answer("\n".join(lines), parse_mode="HTML")


# ── TWA dan kelgan web_app_data ────────────────────────────────────────────────

@router.message(F.web_app_data)
async def handle_web_app_data(message: types.Message, bot):
    """
    Frontend (index.html) dan kelgan JSON ma'lumotni qayta ishlaydi.

    Qo'llab-quvvatlanadigan actionlar:
      - download_mod : Faylni to'g'ridan Telegram DM da yuboradi (file_id orqali)
      - get_orders   : Foydalanuvchi yuklab olgan modlar ro'yxati
      - open_ai      : AI maslahatchi ochish
    """
    try:
        data = json.loads(message.web_app_data.data)
    except json.JSONDecodeError:
        await message.answer("Noto'g'ri ma'lumot formati.")
        return

    action  = data.get("action")
    user_id = message.from_user.id

    # ── Yuklab olish — asosiy funksiya ──────────────────────────────────────
    if action == "download_mod":
        mod_id = data.get("mod_id")
        mod    = await db.get_mod_by_id(mod_id)

        if not mod:
            await message.answer("Mod topilmadi. Iltimos, katalogni yangilang.")
            return

        # Demo mod (real file_id yo'q) ni tekshirish
        if not mod["file_id"] or mod["file_id"].startswith("demo_"):
            await message.answer(
                f"<b>{mod['name']}</b>\n\n"
                f"Bu mod hali mavjud emas yoki demo rejimda.\n"
                f"Tez orada qo'shiladi!",
                parse_mode="HTML",
            )
            return

        # Foydalanuvchiga modni yuborish — Telegram serveri faylni o'tkazadi,
        # bot YUKLAB OLMAYDI, to'g'ridan-to'g'ri file_id orqali yuboradi (2GB gacha)
        price_str = f"{mod['price']:,}".replace(",", " ") + " UZS" if mod["price"] else "Bepul"

        try:
            await bot.send_document(
                chat_id  = user_id,
                document = mod["file_id"],
                caption  = (
                    f"<b>{mod['name']}</b>\n"
                    f"Kategoriya: {mod['category']}   |   {price_str}\n\n"
                    f"{mod.get('description', '')}\n\n"
                    f"BeamModsStudio orqali yuklandi"
                ),
                parse_mode = "HTML",
            )
        except Exception as e:
            await message.answer(
                "Faylni yuborishda xatolik yuz berdi. "
                "Iltimos, keyinroq urinib ko'ring."
            )
            print(f"[Delivery] Xatolik user={user_id} mod={mod_id}: {e}")

    # ── Yuklab olingan modlar ro'yxati ──────────────────────────────────────
    elif action == "get_orders":
        try:
            orders = await db.get_user_purchases(user_id)
        except Exception:
            orders = []

        if not orders:
            await message.answer(
                "Siz hali hech qanday mod yuklamadingiz.\n"
                "Katalogni ko'rish uchun Web App ni oching."
            )
        else:
            lines = [f"<b>Yuklab olingan modlar ({len(orders)} ta):</b>\n"]
            for o in orders:
                lines.append(f"- {o['name']}")
            await message.answer("\n".join(lines), parse_mode="HTML")

    # ── AI maslahatchi ──────────────────────────────────────────────────────
    elif action == "open_ai":
        await message.answer(
            "AI maslahatchi:\n"
            "Savolingizni yozing va men javob beraman!",
        )

    # ── Noma'lum action ──────────────────────────────────────────────────────
    else:
        await message.answer(f"Noma'lum buyruq: {action}")


# ── Buyurtma bekor qilish callback ────────────────────────────────────────────

@router.callback_query(F.data.startswith("cancel_order:"))
async def cancel_order(callback: types.CallbackQuery):
    """Foydalanuvchi buyurtmani bekor qiladi."""
    await callback.message.edit_text(
        "❌ Buyurtma bekor qilindi.",
        reply_markup=None,
    )
    await callback.answer()
