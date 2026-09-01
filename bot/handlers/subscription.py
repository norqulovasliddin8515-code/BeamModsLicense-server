"""
handlers/subscription.py — Obuna tizimi handlerlari.

Buyruqlar:
  /plan    — Joriy obuna holati
  /upgrade — Tarif rejalarini ko'rish va Pro/Max sotib olish

Dekorator:
  @require_tier("pro")  — Handler ga kirish nazorati
  @require_tier("max")

Oqim:
  1. Foydalanuvchi /upgrade bosadi
  2. Bot tariflarni ko'rsatadi (inline tugmalar)
  3. Foydalanuvchi Pro yoki Max tanlaydi
  4. Bot Click.uz to'lov havolasini yuboradi
  5. To'lov webhookida upgrade_subscription() chaqiriladi
"""
import functools
import logging
from datetime import date

from aiogram import Router, F, types, Bot
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.config import ADMIN_ID
from bot import database as db

router = Router()
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
#  Yordamchi: tier belgisi/nomi
# ─────────────────────────────────────────────────────────────────────

def _plan_text(sub: dict) -> str:
    """Obuna ma'lumotlaridan chiroyli matn yaratadi."""
    tier   = sub["tier"]
    info   = sub["info"]
    expire = sub["expire"]

    if expire and date.fromisoformat(expire) >= date.today():
        days_left = (date.fromisoformat(expire) - date.today()).days
        expire_str = f"{expire}  ({days_left} kun qoldi)"
    elif expire:
        expire_str = f"{expire}  (muddati tugagan)"
    else:
        expire_str = "Cheksiz"

    ai_no  = "Yo'q"
    ai_yes = "Ha"
    limits = (
        f"Mod yuklab olish: <b>{info['mods_limit']}</b> ta/oy\n"
        f"AI maslahatchi:  <b>{ ai_yes if info['ai_access'] else ai_no }</b>"
    )

    return (
        f"{info['emoji']} <b>BeamModsStudio — {info['name']} Tarif</b>\n\n"
        f"Obuna darajasi: <b>{info['name']}</b>\n"
        f"Amal qilish muddati: <b>{expire_str}</b>\n\n"
        f"{limits}"
    )


# ─────────────────────────────────────────────────────────────────────
#  /plan — Joriy obuna holati
# ─────────────────────────────────────────────────────────────────────

@router.message(Command("plan"))
async def cmd_plan(message: types.Message):
    """Foydalanuvchining joriy obuna holatini ko'rsatadi."""
    sub = await db.get_subscription(message.from_user.id)
    text = _plan_text(sub)

    # Agar free bo'lsa, upgrade taklifini qo'shamiz
    kb = None
    if sub["tier"] == "free":
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Tariflarni ko'rish", callback_data="show_plans")
        ]])

    await message.answer(text, parse_mode="HTML", reply_markup=kb)


# ─────────────────────────────────────────────────────────────────────
#  /upgrade — Tariflarni ko'rsatish
# ─────────────────────────────────────────────────────────────────────

def _plans_keyboard() -> InlineKeyboardMarkup:
    """Pro va Max tariflar uchun inline tugmalar."""
    pro_info = db.TIERS["pro"]
    max_info = db.TIERS["max"]
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=f"⭐ Pro — {pro_info['price_uzs']:,} UZS/oy".replace(",", " "),
                callback_data="buy_pro",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"💎 Max — {max_info['price_uzs']:,} UZS/oy".replace(",", " "),
                callback_data="buy_max",
            )
        ],
        [
            InlineKeyboardButton(text="Joriy rejam", callback_data="show_current_plan")
        ],
    ])


@router.message(Command("upgrade"))
async def cmd_upgrade(message: types.Message):
    """Obuna tariflarini ko'rsatadi."""
    text = (
        "<b>BeamModsStudio Tariflar</b>\n\n"
        "🆓 <b>Free</b>\n"
        "   • 3 ta mod/oy yuklab olish\n"
        "   • AI maslahatchi: Yo'q\n"
        "   • Narx: Bepul\n\n"
        "⭐ <b>Pro</b>\n"
        "   • 20 ta mod/oy yuklab olish\n"
        "   • AI maslahatchi: Ha\n"
        "   • Narx: 29 900 UZS/oy\n\n"
        "💎 <b>Max</b>\n"
        "   • Cheksiz mod yuklab olish\n"
        "   • AI maslahatchi: Ha\n"
        "   • Barcha yangi modlarga erta kirish\n"
        "   • Narx: 59 900 UZS/oy\n\n"
        "Quyidan kerakli tarifni tanlang:"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=_plans_keyboard())


# ─────────────────────────────────────────────────────────────────────
#  Callback: Tarifni ko'rsatish va sotib olish
# ─────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "show_plans")
async def cb_show_plans(callback: types.CallbackQuery):
    await callback.answer()
    await cmd_upgrade(callback.message)


@router.callback_query(F.data == "show_current_plan")
async def cb_show_current_plan(callback: types.CallbackQuery):
    await callback.answer()
    sub = await db.get_subscription(callback.from_user.id)
    await callback.message.answer(_plan_text(sub), parse_mode="HTML")


@router.callback_query(F.data.in_({"buy_pro", "buy_max"}))
async def cb_buy_plan(callback: types.CallbackQuery):
    """Foydalanuvchi Pro yoki Max tanladi — to'lov havolasini yuboradi."""
    await callback.answer()
    tier     = "pro" if callback.data == "buy_pro" else "max"
    tier_info = db.TIERS[tier]
    user_id  = callback.from_user.id
    price    = tier_info["price_uzs"]

    # ── Click.uz to'lov havolasi ──────────────────────────────────
    # Haqiqiy loyihada bu Click yoki Payme havolasi bo'ladi.
    # Format: https://my.click.uz/services/pay?service_id=...&amount=...&transaction_param=...
    # transaction_param sifatida "sub_{user_id}_{tier}" formatini ishlatamiz
    transaction_param = f"sub_{user_id}_{tier}"

    # TODO: .env dan CLICK_SERVICE_ID ni oling
    from bot.config import CLICK_SERVICE_ID, CLICK_MERCHANT_ID
    click_url = (
        f"https://my.click.uz/services/pay"
        f"?service_id={CLICK_SERVICE_ID}"
        f"&merchant_id={CLICK_MERCHANT_ID}"
        f"&amount={price}"
        f"&transaction_param={transaction_param}"
        f"&return_url=https://t.me/BeamModsStudio_bot"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Click.uz orqali to'lash", url=click_url)],
        [InlineKeyboardButton(text="Orqaga", callback_data="show_plans")],
    ])

    await callback.message.answer(
        f"{tier_info['emoji']} <b>{tier_info['name']} Tarif</b>\n\n"
        f"Narx: <b>{price:,} UZS/oy</b>\n\n".replace(",", " ") +
        f"To'lov tugmasini bosing. To'lovdan so'ng obunangiz "
        f"<b>avtomatik 30 kun</b> ga faollashadi.",
        parse_mode="HTML",
        reply_markup=kb,
    )


# ─────────────────────────────────────────────────────────────────────
#  Admin: Manuel upgrade (test yoki sovg'a uchun)
#  /giveplan <user_id> <tier> [days]
# ─────────────────────────────────────────────────────────────────────

@router.message(
    F.text.startswith("/giveplan"),
    F.from_user.id == ADMIN_ID,
)
async def cmd_giveplan(message: types.Message, bot: Bot):
    """
    Admin manual obuna beradi.
    Ishlatish: /giveplan <user_id> pro|max [kunlar=30]
    """
    parts = message.text.split()
    if len(parts) < 3:
        await message.answer(
            "Ishlatish: /giveplan &lt;user_id&gt; pro|max [kunlar]\n"
            "Misol: /giveplan 123456789 pro 30",
            parse_mode="HTML",
        )
        return

    try:
        target_id = int(parts[1])
        tier      = parts[2].lower()
        days      = int(parts[3]) if len(parts) > 3 else 30
    except (ValueError, IndexError):
        await message.answer("Noto'g'ri format.")
        return

    if tier not in ("pro", "max"):
        await message.answer("Tier 'pro' yoki 'max' bo'lishi kerak.")
        return

    expire = await db.upgrade_subscription(target_id, tier, days)

    await message.answer(
        f"Muvaffaqiyatli!\n"
        f"User: <code>{target_id}</code>\n"
        f"Tier: <b>{tier.upper()}</b>\n"
        f"Muddati: <b>{expire}</b> ({days} kun)",
        parse_mode="HTML",
    )

    # Foydalanuvchiga xabar berish
    try:
        tier_info = db.TIERS[tier]
        await bot.send_message(
            target_id,
            f"{tier_info['emoji']} <b>Tabriklaymiz!</b>\n\n"
            f"BeamModsStudio <b>{tier_info['name']}</b> obunasi faollashtirildi!\n"
            f"Muddati: <b>{expire}</b>\n\n"
            f"/plan — Obuna holatini ko'rish",
            parse_mode="HTML",
        )
    except Exception:
        pass  # Foydalanuvchi boti bloklagan bo'lishi mumkin


# ─────────────────────────────────────────────────────────────────────
#  Dekorator: Handler ga kirish nazorati
# ─────────────────────────────────────────────────────────────────────

def require_tier(min_tier: str):
    """
    Handler uchun dekorator — faqat ma'lum tier va yuqorisi uchun ruxsat beradi.

    Ishlatish:
        @router.message(Command("premium_feature"))
        @require_tier("pro")
        async def premium_handler(message: types.Message):
            ...

    Tier tartib: free < pro < max
    """
    TIER_ORDER = {"free": 0, "pro": 1, "max": 2}

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(message: types.Message, *args, **kwargs):
            sub = await db.get_subscription(message.from_user.id)
            user_level = TIER_ORDER.get(sub["tier"], 0)
            need_level = TIER_ORDER.get(min_tier, 0)

            if user_level >= need_level:
                return await func(message, *args, **kwargs)
            else:
                # Ruxsat yo'q
                needed_info = db.TIERS.get(min_tier, {})
                kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(
                        text=f"{needed_info.get('emoji','')} {min_tier.upper()} olish",
                        callback_data=f"buy_{min_tier}",
                    )
                ]])
                await message.answer(
                    f"Bu xususiyat faqat <b>{min_tier.upper()}</b> va yuqori tarif uchun.\n\n"
                    f"Joriy tarifingiz: <b>{sub['tier'].upper()}</b>\n\n"
                    f"/upgrade — Tarifni yaxshilash",
                    parse_mode="HTML",
                    reply_markup=kb,
                )
        return wrapper
    return decorator
