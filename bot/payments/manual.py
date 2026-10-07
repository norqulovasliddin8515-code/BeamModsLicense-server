"""
manual.py — "Card Transfer (UZS)" manual payment with admin approval.

Flow:
  1. user picks Card Transfer -> a pending card payment is created and the bot
     DMs the card number + exact UZS amount + "send a screenshot here".
  2. user sends a photo -> it attaches to their latest awaiting-receipt payment,
     status becomes pending_manual_verification, and the receipt is forwarded to
     the admin with Approve / Reject buttons.
  3. admin Approve -> status paid + core.fulfill (same DRM delivery as Stars).
     admin Reject  -> status rejected + the user is told.

No FSM: the receipt is matched to the user's most recent awaiting-receipt card
payment, so it works whether the payment was started from the bot or the Mini App.
"""
import html
import logging

from aiogram import Bot, F, Router, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.config import ADMIN_ID, CARD_NUMBER, CARD_HOLDER
from bot.payments import core

logger = logging.getLogger(__name__)
router = Router(name="manual_pay")


async def _label(intent: str) -> str:
    from bot import database as db
    kind, target = intent.split(":", 1)
    if kind == "sub":
        info = db.TIERS.get(target, {})
        return f"{info.get('emoji', '')} {info.get('name', target.upper())} obuna (30 kun)"
    mod = await db.get_mod_by_id(int(target))
    return f"📦 {mod['name']}" if mod else f"mod #{target}"


async def start_card_payment(bot: Bot, user_id: int, intent: str) -> core.Payment:
    """Create a pending card payment and DM the buyer the card details."""
    amount_uzs = await core.price_for_intent(intent)
    payment = await core.create_payment(user_id, intent, amount_uzs, provider="card")
    label = await _label(intent)
    holder = f"\nKarta egasi: <b>{html.escape(CARD_HOLDER)}</b>" if CARD_HOLDER else ""
    await bot.send_message(
        user_id,
        f"💳 <b>Karta orqali to'lov</b>\n\n"
        f"{label}\n"
        f"Summa: <b>{amount_uzs:,} UZS</b>\n\n".replace(",", " ") +
        f"Karta raqami:\n<code>{CARD_NUMBER}</code>{holder}\n\n"
        f"1️⃣ Yuqoridagi kartaga <b>aniq {amount_uzs:,} UZS</b> o'tkazing.\n".replace(",", " ") +
        f"2️⃣ Chek (skrinshot) ni shu yerga <b>rasm</b> qilib yuboring.\n\n"
        f"Admin tasdiqlagach, mod/obuna avtomatik yetkaziladi. (Buyurtma #{payment.id})",
        parse_mode="HTML",
    )
    logger.info(f"[manual] started card payment #{payment.id} user={user_id} intent={intent}")
    return payment


@router.message(F.chat.type == "private", F.photo)
async def on_receipt(message: types.Message, bot: Bot):
    """A private photo from a user who has a card payment awaiting a receipt."""
    payment = await core.latest_awaiting_receipt(message.from_user.id)
    if not payment:
        return  # not part of a card flow — let other handlers deal with it

    file_id = message.photo[-1].file_id
    if not await core.attach_receipt(payment.id, file_id):
        return
    await message.answer(
        "✅ Chek qabul qilindi. Admin tekshirgach xabar beramiz. Rahmat!",
    )

    label = await _label(payment.intent)
    u = message.from_user
    uname = f"@{u.username}" if u.username else "—"
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Approve", callback_data=f"mapprove:{payment.id}"),
        InlineKeyboardButton(text="❌ Reject",  callback_data=f"mreject:{payment.id}"),
    ]])
    caption = (
        f"🧾 <b>Manual to'lov tasdig'i</b>  (#{payment.id})\n\n"
        f"Foydalanuvchi: <a href=\"tg://user?id={u.id}\">{html.escape(u.full_name)}</a> ({uname})\n"
        f"ID: <code>{u.id}</code>\n"
        f"Mahsulot: {label}\n"
        f"Summa: <b>{payment.amount_uzs:,} UZS</b>".replace(",", " ")
    )
    try:
        sent = await bot.send_photo(ADMIN_ID, photo=file_id, caption=caption, reply_markup=kb)
        await core.set_admin_msg(payment.id, sent.chat.id, sent.message_id)
    except Exception as e:
        logger.error(f"[manual] could not notify admin for #{payment.id}: {e}")


@router.callback_query(F.data.startswith("mapprove:"), F.from_user.id == ADMIN_ID)
async def on_approve(callback: types.CallbackQuery, bot: Bot):
    pid = _pid(callback.data)
    payment = await core.get_payment(pid) if pid else None
    if not payment:
        await callback.answer("Topilmadi", show_alert=True)
        return
    if payment.status != core.STATUS_AWAIT_VERIFY:
        await callback.answer(f"Allaqachon: {payment.status}", show_alert=True)
        return

    if not await core.approve_manual(pid):
        await callback.answer("Holat o'zgargan", show_alert=True)
        return
    await callback.answer("Tasdiqlandi ✅")
    try:
        await core.fulfill(bot, await core.get_payment(pid))
    except Exception:
        logger.exception(f"[manual] fulfillment failed after approve #{pid}")
        await bot.send_message(ADMIN_ID, f"⚠️ #{pid} tasdiqlandi, lekin yetkazishda xato. Loglarni tekshiring.")
    await _mark_admin_msg(callback, "✅ APPROVED")


@router.callback_query(F.data.startswith("mreject:"), F.from_user.id == ADMIN_ID)
async def on_reject(callback: types.CallbackQuery, bot: Bot):
    pid = _pid(callback.data)
    payment = await core.get_payment(pid) if pid else None
    if not payment:
        await callback.answer("Topilmadi", show_alert=True)
        return
    if payment.status != core.STATUS_AWAIT_VERIFY:
        await callback.answer(f"Allaqachon: {payment.status}", show_alert=True)
        return

    await core.reject_manual(pid)
    await callback.answer("Rad etildi ❌")
    try:
        await bot.send_message(
            payment.user_id,
            f"❌ Buyurtma #{pid} bo'yicha to'lov tasdiqlanmadi.\n"
            "Agar bu xato deb hisoblasangiz, admin bilan bog'laning.",
        )
    except Exception:
        pass
    await _mark_admin_msg(callback, "❌ REJECTED")


def _pid(data: str):
    tail = data.split(":", 1)[1]
    return int(tail) if tail.isdigit() else None


async def _mark_admin_msg(callback: types.CallbackQuery, status: str) -> None:
    try:
        base = callback.message.caption or callback.message.text or ""
        await callback.message.edit_caption(
            caption=base + f"\n\n— {status} by admin",
            reply_markup=None,
        )
    except Exception:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
