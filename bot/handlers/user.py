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
async def handle_web_app_data(message: types.Message):
    """
    Frontend (index.html) dan kelgan JSON ma'lumotni qayta ishlaydi.
    Qo'llab-quvvatlanadigan action lar:
      - get_catalog   : Barcha modlarni JSON qaytaradi (API o'rniga)
      - buy_mod       : Buyurtma yaratadi va to'lov havolasini yuboradi
      - ai_query      : AI ga savol yuboradi
    """
    try:
        data = json.loads(message.web_app_data.data)
    except json.JSONDecodeError:
        await message.answer("❗ Noto'g'ri ma'lumot formati.")
        return

    action = data.get("action")

    # ── Katalog so'rovi ──────────────────────────────────────────────────────
    if action == "get_catalog":
        mods = await db.get_all_mods(category=data.get("category"))
        # Haqiqiy loyihada bu yerda API endpoint ishlatiladi; MVP uchun bot xabar beradi
        await message.answer(
            f"📂 Katalog: <b>{len(mods)} ta mod</b> mavjud. "
            f"Batafsil ko'rish uchun Web App ni oching.",
            parse_mode="HTML",
        )

    # ── Mod sotib olish ──────────────────────────────────────────────────────
    elif action == "buy_mod":
        mod_id = data.get("mod_id")
        payment_method = data.get("payment_method", "click")

        mod = await db.get_mod_by_id(mod_id)
        if not mod:
            await message.answer("❗ Mod topilmadi.")
            return

        # Avval sotib olganmi tekshirish
        already_bought = await db.has_user_purchased(message.from_user.id, mod_id)
        if already_bought:
            await message.answer(
                f"✅ Siz bu modni allaqachon sotib olgansiz!\n"
                f"Faylni qayta olish uchun /myfiles yuboring."
            )
            return

        # Buyurtma yaratish
        order_id = await db.create_order(
            user_id        = message.from_user.id,
            mod_id         = mod_id,
            amount         = mod["price"],
            payment_method = payment_method,
        )

        price_formatted = f"{mod['price']:,}".replace(",", " ")

        # To'lov havolasini yuborish (payment.py da batafsil)
        from bot.handlers.payment import generate_payment_url
        payment_url = await generate_payment_url(
            order_id       = order_id,
            amount         = mod["price"],
            payment_method = payment_method,
            description    = f"BeamModsStudio: {mod['name']}",
        )

        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text = f"💳 To'lash — {price_formatted} UZS",
                url  = payment_url,
            )],
            [InlineKeyboardButton(
                text          = "❌ Bekor qilish",
                callback_data = f"cancel_order:{order_id}",
            )],
        ])

        await message.answer(
            f"🛒 <b>Buyurtma #{order_id}</b>\n\n"
            f"📦 Mod: <b>{mod['name']}</b>\n"
            f"💵 Narx: <b>{price_formatted} UZS</b>\n"
            f"💳 To'lov usuli: <b>{payment_method.capitalize()}</b>\n\n"
            f"To'lash uchun quyidagi tugmani bosing 👇",
            parse_mode   = "HTML",
            reply_markup = markup,
        )

    # ── Noma'lum action ──────────────────────────────────────────────────────
    else:
        await message.answer(f"❓ Noma'lum buyruq: {action}")


# ── Buyurtma bekor qilish callback ────────────────────────────────────────────

@router.callback_query(F.data.startswith("cancel_order:"))
async def cancel_order(callback: types.CallbackQuery):
    """Foydalanuvchi buyurtmani bekor qiladi."""
    await callback.message.edit_text(
        "❌ Buyurtma bekor qilindi.",
        reply_markup=None,
    )
    await callback.answer()
