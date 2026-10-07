"""
stars.py — Telegram Stars (XTR) payments, for global users.

Telegram Stars need no payment provider and no provider_token: send an invoice
with currency "XTR", approve the PreCheckoutQuery, then fulfil on the
successful_payment update. Telegram signs these updates itself (they arrive over
the authenticated bot connection), so there is no webhook signature to verify —
only idempotency, which core.fulfill already guarantees.

Star price is derived from the UZS price via STARS_UZS_RATE so you keep one
price list. Tune the rate in .env to match the Stars→USD→UZS rate you want.
"""
import logging
import math

from aiogram import F, Router, types
from aiogram.types import LabeledPrice

from bot.config import STARS_UZS_RATE
from bot.payments import core

logger = logging.getLogger(__name__)
router = Router(name="stars")

TITLES = {"sub:pro": "Pro obuna (30 kun)", "sub:max": "Max obuna (30 kun)"}


def stars_for_uzs(amount_uzs: int) -> int:
    return max(1, math.ceil(amount_uzs / STARS_UZS_RATE))


async def send_stars_invoice(bot, chat_id: int, intent: str, title: str, description: str) -> core.Payment:
    """Create a pending payment and send its Stars invoice. payload = payment id."""
    amount_uzs = await core.price_for_intent(intent)
    payment = await core.create_payment(chat_id, intent, amount_uzs, provider="stars")
    stars = stars_for_uzs(amount_uzs)
    await bot.send_invoice(
        chat_id=chat_id,
        title=title[:32],
        description=description[:255],
        payload=f"pay:{payment.id}",
        provider_token="",              # empty for Stars
        currency="XTR",
        prices=[LabeledPrice(label=title[:32], amount=stars)],
    )
    logger.info(f"[stars] invoice #{payment.id} intent={intent} stars={stars}")
    return payment


@router.pre_checkout_query()
async def on_pre_checkout(pcq: types.PreCheckoutQuery):
    """Last chance to reject. Confirm the payment still exists and is pending."""
    payment = _payment_id_from_payload(pcq.invoice_payload)
    ok = False
    if payment is not None:
        p = await core.get_payment(payment)
        ok = bool(p and p.status == "pending")
    try:
        await pcq.answer(ok=ok, error_message=None if ok else "To'lov yaroqsiz. Qaytadan urining.")
    except Exception as e:
        logger.warning(f"[stars] pre_checkout answer failed: {e}")


@router.message(F.successful_payment)
async def on_success(message: types.Message, bot):
    sp = message.successful_payment
    pid = _payment_id_from_payload(sp.invoice_payload)
    if pid is None:
        logger.error(f"[stars] bad payload: {sp.invoice_payload!r}")
        return
    payment = await core.get_payment(pid)
    if not payment:
        logger.error(f"[stars] payment #{pid} not found")
        return
    won = await core.mark_paid(pid, "stars", sp.telegram_payment_charge_id)
    # Fulfil whether or not we won the flip (idempotent); covers retries.
    await core.fulfill(bot, await core.get_payment(pid))
    logger.info(f"[stars] paid #{pid} charge={sp.telegram_payment_charge_id} won={won}")


def _payment_id_from_payload(payload: str):
    if isinstance(payload, str) and payload.startswith("pay:") and payload[4:].isdigit():
        return int(payload[4:])
    return None
