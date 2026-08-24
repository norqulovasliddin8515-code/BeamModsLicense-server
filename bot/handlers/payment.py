"""
payment.py — Click.uz va Payme to'lov integratsiyasi skelet.

Arxitektura:
  1. generate_payment_url()  — To'lov sahifasiga URL yaratadi
  2. click_webhook()         — Click.uz dan kelgan webhook ni tekshiradi va tasdiqlaydi
  3. payme_webhook()         — Payme dan kelgan webhook ni tekshiradi va tasdiqlaydi
  4. Har ikkala webhook ham muvaffaqiyatda delivery.send_file_to_user() ni chaqiradi

MUHIM: Real production uchun HTTPS va to'g'ri imzo tekshiruvlari kerak!
"""
import hashlib
import hmac
import json
import base64
from aiohttp import web

from bot.config import (
    CLICK_SERVICE_ID, CLICK_MERCHANT_ID, CLICK_SECRET_KEY,
    PAYME_MERCHANT_ID, PAYME_SECRET_KEY,
)
from bot import database as db
from bot.handlers import delivery


# ── To'lov URL generatsiyasi ──────────────────────────────────────────────────

async def generate_payment_url(
    order_id: int,
    amount: int,
    payment_method: str,
    description: str,
) -> str:
    """
    To'lov tizimi uchun URL yaratadi.
    amount: UZS da (tiyin uchun *100 qilish kerak)
    """
    amount_tiyin = amount * 100  # UZS → tiyin

    if payment_method == "click":
        # Click.uz to'lov URL formati
        # https://my.click.uz/services/pay?service_id=...&merchant_id=...&amount=...&transaction_param=...
        url = (
            f"https://my.click.uz/services/pay"
            f"?service_id={CLICK_SERVICE_ID}"
            f"&merchant_id={CLICK_MERCHANT_ID}"
            f"&amount={amount_tiyin / 100:.2f}"      # Click UZS qabul qiladi
            f"&transaction_param={order_id}"
            f"&return_url=https://t.me/BeamModsStudioBot"
        )
        return url

    elif payment_method == "payme":
        # Payme URL formati (base64 encoded params)
        params = {
            "m": PAYME_MERCHANT_ID,
            "ac.order_id": str(order_id),
            "a": amount_tiyin,           # Payme tiyin qabul qiladi
            "l": "uz",
            "c": "https://t.me/BeamModsStudioBot",
        }
        # Payme params ni base64 ga encode qilish
        param_str = ";".join(f"{k}={v}" for k, v in params.items())
        encoded   = base64.b64encode(param_str.encode()).decode()
        return f"https://checkout.paycom.uz/{encoded}"

    # Fallback
    return "https://t.me/BeamModsStudioBot"


# ── Click.uz Webhook Handler ──────────────────────────────────────────────────

async def click_prepare(request: web.Request) -> web.Response:
    """
    Click.uz PREPARE so'rovi — tranzaksiya boshlanganda keladi.
    Buyurtma mavjudligini tekshirib, 0 (muvaffaqiyat) qaytaradi.
    """
    data = await request.post()

    click_trans_id    = data.get("click_trans_id")
    service_id        = data.get("service_id")
    merchant_trans_id = data.get("merchant_trans_id")  # Bu bizning order_id
    amount            = data.get("amount")
    action            = data.get("action")          # 0 = prepare
    sign_time         = data.get("sign_time")
    sign_string       = data.get("sign_string")

    # Imzoni tekshirish
    expected_sign = hashlib.md5(
        f"{click_trans_id}{service_id}{CLICK_SECRET_KEY}"
        f"{merchant_trans_id}{amount}{action}{sign_time}".encode()
    ).hexdigest()

    if sign_string != expected_sign:
        return web.json_response({"error": -1, "error_note": "Noto'g'ri imzo"})

    # Buyurtma mavjudligini tekshirish
    order = await db.get_order_by_id(int(merchant_trans_id))
    if not order:
        return web.json_response({"error": -5, "error_note": "Buyurtma topilmadi"})

    if order["payment_status"] == "paid":
        return web.json_response({"error": -4, "error_note": "Allaqachon to'langan"})

    return web.json_response({
        "error":               0,
        "error_note":          "Success",
        "click_trans_id":      click_trans_id,
        "merchant_trans_id":   merchant_trans_id,
        "merchant_prepare_id": merchant_trans_id,
    })


async def click_complete(request: web.Request, bot) -> web.Response:
    """
    Click.uz COMPLETE so'rovi — to'lov muvaffaqiyatli bo'lganda keladi.
    Faylni foydalanuvchiga yuboradi.
    """
    data = await request.post()

    click_trans_id    = data.get("click_trans_id")
    merchant_trans_id = data.get("merchant_trans_id")
    error             = data.get("error")

    # Xatolik bo'lsa
    if error and int(error) < 0:
        return web.json_response({
            "error":             int(error),
            "error_note":        "Bekor qilindi",
            "click_trans_id":    click_trans_id,
            "merchant_trans_id": merchant_trans_id,
        })

    order_id = int(merchant_trans_id)
    order    = await db.get_order_by_id(order_id)

    if not order or order["payment_status"] == "paid":
        return web.json_response({
            "error":             -4,
            "click_trans_id":    click_trans_id,
            "merchant_trans_id": merchant_trans_id,
        })

    # Buyurtmani to'langan deb belgilash
    await db.mark_order_paid(order_id, transaction_id=str(click_trans_id))

    # ✅ Faylni avtomatik yuborish
    await delivery.send_file_to_user(
        bot     = bot,
        user_id = order["user_id"],
        mod_id  = order["mod_id"],
    )

    return web.json_response({
        "error":               0,
        "error_note":          "Success",
        "click_trans_id":      click_trans_id,
        "merchant_trans_id":   merchant_trans_id,
        "merchant_confirm_id": merchant_trans_id,
    })


# ── Payme Webhook Handler ─────────────────────────────────────────────────────

async def payme_webhook(request: web.Request, bot) -> web.Response:
    """
    Payme JSON-RPC webhook handlerı.
    Qo'llab-quvvatlanadigan metodlar: CheckPerformTransaction,
    CreateTransaction, PerformTransaction, CancelTransaction.
    """
    # Basic auth tekshirish
    auth = request.headers.get("Authorization", "")
    if auth:
        try:
            decoded  = base64.b64decode(auth.split(" ")[1]).decode()
            _, token = decoded.split(":")
            if token != PAYME_SECRET_KEY:
                return web.json_response({"error": {"code": -32504, "message": "Ruxsat yo'q"}})
        except Exception:
            return web.json_response({"error": {"code": -32504, "message": "Noto'g'ri auth"}})

    body   = await request.json()
    method = body.get("method")
    params = body.get("params", {})
    rpc_id = body.get("id")

    order_id = int(params.get("account", {}).get("order_id", 0))
    order    = await db.get_order_by_id(order_id)

    def resp(result=None, error=None):
        response = {"jsonrpc": "2.0", "id": rpc_id}
        if error:
            response["error"] = error
        else:
            response["result"] = result
        return web.json_response(response)

    # ── CheckPerformTransaction ──────────────────────────────────────────────
    if method == "CheckPerformTransaction":
        if not order:
            return resp(error={"code": -31050, "message": "Buyurtma topilmadi"})
        if order["payment_status"] == "paid":
            return resp(error={"code": -31051, "message": "Allaqachon to'langan"})
        return resp(result={"allow": True})

    # ── CreateTransaction ────────────────────────────────────────────────────
    elif method == "CreateTransaction":
        if not order:
            return resp(error={"code": -31050, "message": "Buyurtma topilmadi"})
        return resp(result={
            "create_time": int(__import__("time").time() * 1000),
            "transaction": str(order_id),
            "state":       1,
        })

    # ── PerformTransaction — to'lov muvaffaqiyatli ───────────────────────────
    elif method == "PerformTransaction":
        if not order:
            return resp(error={"code": -31050, "message": "Buyurtma topilmadi"})

        payme_trans_id = params.get("id", "")
        await db.mark_order_paid(order_id, transaction_id=payme_trans_id)

        # ✅ Faylni avtomatik yuborish
        await delivery.send_file_to_user(
            bot     = bot,
            user_id = order["user_id"],
            mod_id  = order["mod_id"],
        )

        return resp(result={
            "perform_time": int(__import__("time").time() * 1000),
            "transaction":  str(order_id),
            "state":        2,
        })

    # ── CancelTransaction ────────────────────────────────────────────────────
    elif method == "CancelTransaction":
        return resp(result={
            "cancel_time": int(__import__("time").time() * 1000),
            "transaction": str(order_id),
            "state":       -1,
        })

    return resp(error={"code": -32601, "message": "Noma'lum metod"})
