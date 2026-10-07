"""
click.py — Click.uz Merchant (SHOP) API: Prepare + Complete.

Signature (Click concatenates the amount EXACTLY as sent, so we sign the raw
string, never a reparsed float):
    prepare  (action=0): md5(click_trans_id + service_id + SECRET + merchant_trans_id
                              + amount + action + sign_time)
    complete (action=1): md5(click_trans_id + service_id + SECRET + merchant_trans_id
                              + merchant_prepare_id + amount + action + sign_time)

Error codes: 0 ok, -1 bad sign, -2 bad amount, -4 already paid,
             -5 order not found, -6 transaction not found, -9 cancelled.

The old skeleton checked the signature on prepare but NOT on complete, and
never compared the amount — so a forged complete POST delivered a mod for free.
Both are enforced here.
"""
import hashlib
import logging
from decimal import Decimal, InvalidOperation

from aiohttp import web

from bot.config import CLICK_SERVICE_ID, CLICK_SECRET_KEY
from bot.payments import core

logger = logging.getLogger(__name__)


def _md5(*parts: str) -> str:
    return hashlib.md5("".join(parts).encode()).hexdigest()


def _err(code: int, note: str, **extra):
    return web.json_response({"error": code, "error_note": note, **extra})


def _amount_matches(raw_amount: str, payment: core.Payment) -> bool:
    try:
        got = Decimal(raw_amount)
    except (InvalidOperation, TypeError):
        return False
    return got == Decimal(payment.amount_uzs)   # Click bills in UZS (not tiyin)


async def prepare(request: web.Request) -> web.Response:
    d = await request.post()
    f = lambda k: str(d.get(k, ""))
    click_trans_id, service_id = f("click_trans_id"), f("service_id")
    merchant_trans_id, amount = f("merchant_trans_id"), f("amount")
    action, sign_time, sign = f("action"), f("sign_time"), f("sign_string")

    expected = _md5(click_trans_id, service_id, CLICK_SECRET_KEY,
                    merchant_trans_id, amount, action, sign_time)
    if not sign or sign.lower() != expected.lower():
        logger.warning(f"[click] bad prepare sign merchant_trans_id={merchant_trans_id}")
        return _err(-1, "SIGN CHECK FAILED")
    if action != "0":
        return _err(-3, "Action not found")
    if not merchant_trans_id.isdigit():
        return _err(-5, "Order not found")

    payment = await core.get_payment(int(merchant_trans_id))
    if not payment:
        return _err(-5, "Order not found")
    if payment.status == "paid":
        return _err(-4, "Already paid")
    if payment.status == "cancelled":
        return _err(-9, "Transaction cancelled")
    if not _amount_matches(amount, payment):
        logger.warning(f"[click] amount mismatch #{payment.id}: got {amount} want {payment.amount_uzs}")
        return _err(-2, "Incorrect parameter amount")

    await core.set_provider(payment.id, "click")
    return web.json_response({
        "error": 0, "error_note": "Success",
        "click_trans_id": click_trans_id,
        "merchant_trans_id": merchant_trans_id,
        "merchant_prepare_id": payment.id,   # echoed back in complete
    })


def make_complete_handler(bot):
    async def complete(request: web.Request) -> web.Response:
        d = await request.post()
        f = lambda k: str(d.get(k, ""))
        click_trans_id, service_id = f("click_trans_id"), f("service_id")
        merchant_trans_id = f("merchant_trans_id")
        merchant_prepare_id, amount = f("merchant_prepare_id"), f("amount")
        action, sign_time, sign = f("action"), f("sign_time"), f("sign_string")
        error = f("error")

        expected = _md5(click_trans_id, service_id, CLICK_SECRET_KEY, merchant_trans_id,
                        merchant_prepare_id, amount, action, sign_time)
        if not sign or sign.lower() != expected.lower():
            logger.warning(f"[click] bad complete sign merchant_trans_id={merchant_trans_id}")
            return _err(-1, "SIGN CHECK FAILED")
        if action != "1":
            return _err(-3, "Action not found")
        if not merchant_trans_id.isdigit():
            return _err(-5, "Order not found")

        payment = await core.get_payment(int(merchant_trans_id))
        if not payment:
            return _err(-5, "Order not found")
        if str(payment.id) != merchant_prepare_id:
            return _err(-6, "Transaction not found")

        # Click tells us the user cancelled / payment failed.
        if error and error.lstrip("-").isdigit() and int(error) < 0:
            await core.mark_cancelled(payment.id)
            return _err(-9, "Transaction cancelled",
                        click_trans_id=click_trans_id, merchant_trans_id=merchant_trans_id)

        if not _amount_matches(amount, payment):
            return _err(-2, "Incorrect parameter amount")

        if payment.status == "paid":
            # Idempotent: Click retries complete until it gets error:0.
            return web.json_response({
                "error": 0, "error_note": "Success",
                "click_trans_id": click_trans_id,
                "merchant_trans_id": merchant_trans_id,
                "merchant_confirm_id": payment.id,
            })

        won = await core.mark_paid(payment.id, "click", click_trans_id)
        if won:
            try:
                await core.fulfill(bot, await core.get_payment(payment.id))
            except Exception:
                logger.exception(f"[click] fulfillment error #{payment.id}")
                # Still ack success to Click (money is captured); retry fulfillment via admin.
        return web.json_response({
            "error": 0, "error_note": "Success",
            "click_trans_id": click_trans_id,
            "merchant_trans_id": merchant_trans_id,
            "merchant_confirm_id": payment.id,
        })

    return complete
