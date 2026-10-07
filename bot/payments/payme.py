"""
payme.py — Payme (Paycom) Merchant API, JSON-RPC 2.0.

Methods: CheckPerformTransaction, CreateTransaction, PerformTransaction,
CancelTransaction, CheckTransaction, GetStatement.

Hardening vs the old skeleton:
  * auth is MANDATORY — the old `if auth:` skipped the check entirely when the
    header was absent, so an unauthenticated POST could Perform and deliver.
  * amount is verified (in tiyin) on CheckPerform and Create.
  * idempotent: the Payme transaction id is stored; repeated Create/Perform/Cancel
    return the stored state instead of acting twice.
  * 12-hour timeout rule (error -31008, cancel reason 4) implemented.

Error codes: -32504 auth, -32601 method, -31001 amount, -31003 txn not found,
-31008 cannot perform now, -31050 order not found, -31099 order unavailable.
"""
import base64
import hmac
import logging
import time

from aiohttp import web

from bot.config import PAYME_SECRET_KEY
from bot.payments import core
from bot import db

logger = logging.getLogger(__name__)

TIMEOUT_MS = 12 * 60 * 60 * 1000   # Payme requires auto-cancel after 12h in state 1

STATE_CREATED = 1
STATE_DONE = 2
STATE_CANCELLED = -1
STATE_CANCELLED_AFTER_DONE = -2


def _now_ms() -> int:
    return int(time.time() * 1000)


def _check_auth(request: web.Request) -> bool:
    """Basic auth; password must equal the Payme key. Constant-time compare."""
    header = request.headers.get("Authorization", "")
    if not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:]).decode()
    except Exception:
        return False
    if ":" not in decoded:
        return False
    _, key = decoded.split(":", 1)
    if not PAYME_SECRET_KEY:
        return False
    return hmac.compare_digest(key, PAYME_SECRET_KEY)


def make_payme_handler(bot):
    async def handler(request: web.Request) -> web.Response:
        rpc_id = None
        if not _check_auth(request):
            return _rpc(None, error=_e(-32504, "Insufficient privileges"))
        try:
            body = await request.json()
        except Exception:
            return _rpc(None, error=_e(-32700, "Parse error"))

        rpc_id = body.get("id")
        method = body.get("method")
        params = body.get("params", {}) or {}

        try:
            if method == "CheckPerformTransaction":
                return await _check_perform(rpc_id, params)
            if method == "CreateTransaction":
                return await _create(rpc_id, params)
            if method == "PerformTransaction":
                return await _perform(rpc_id, params, bot)
            if method == "CancelTransaction":
                return await _cancel(rpc_id, params)
            if method == "CheckTransaction":
                return await _check_txn(rpc_id, params)
            if method == "GetStatement":
                return await _statement(rpc_id, params)
        except Exception:
            logger.exception(f"[payme] handler error method={method}")
            return _rpc(rpc_id, error=_e(-31008, "Internal error"))

        return _rpc(rpc_id, error=_e(-32601, "Method not found"))

    return handler


# ── JSON-RPC plumbing ───────────────────────────────────────────────────────
def _rpc(rpc_id, result=None, error=None) -> web.Response:
    out = {"jsonrpc": "2.0", "id": rpc_id}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result
    return web.json_response(out)


def _e(code: int, message: str, data=None) -> dict:
    msg = {"ru": message, "uz": message, "en": message}
    err = {"code": code, "message": msg}
    if data is not None:
        err["data"] = data
    return err


async def _resolve(params) -> tuple:
    """Return (payment, error_dict|None) from params.account.order_id."""
    account = params.get("account", {}) or {}
    raw = str(account.get("order_id", ""))
    if not raw.isdigit():
        return None, _e(-31050, "Order not found", data="order_id")
    payment = await core.get_payment(int(raw))
    if not payment:
        return None, _e(-31050, "Order not found", data="order_id")
    return payment, None


def _amount_ok(params, payment: core.Payment) -> bool:
    try:
        return int(params.get("amount")) == payment.amount_tiyin
    except (TypeError, ValueError):
        return False


# ── Methods ──────────────────────────────────────────────────────────────────
async def _check_perform(rpc_id, params):
    payment, err = await _resolve(params)
    if err:
        return _rpc(rpc_id, error=err)
    if not _amount_ok(params, payment):
        return _rpc(rpc_id, error=_e(-31001, "Incorrect amount"))
    if payment.status == "paid":
        return _rpc(rpc_id, error=_e(-31099, "Order already paid"))
    if payment.status == "cancelled":
        return _rpc(rpc_id, error=_e(-31099, "Order unavailable"))
    return _rpc(rpc_id, result={"allow": True})


async def _create(rpc_id, params):
    txn_id = str(params.get("id", ""))
    existing = await core.find_by_provider_txn("payme", txn_id)
    if existing:
        # Idempotent replay of Create for a txn we already know.
        if existing.payme_state == STATE_CREATED:
            pm = await _payme_times(existing.id)
            return _rpc(rpc_id, result={
                "create_time": pm["create_ms"], "transaction": str(existing.id), "state": STATE_CREATED})
        return _rpc(rpc_id, error=_e(-31008, "Transaction in invalid state"))

    payment, err = await _resolve(params)
    if err:
        return _rpc(rpc_id, error=err)
    if not _amount_ok(params, payment):
        return _rpc(rpc_id, error=_e(-31001, "Incorrect amount"))
    if payment.status == "paid":
        return _rpc(rpc_id, error=_e(-31099, "Order already paid"))
    if payment.status == "cancelled":
        return _rpc(rpc_id, error=_e(-31099, "Order unavailable"))
    if payment.provider_txn_id and payment.provider == "payme":
        # Another Payme txn already owns this order.
        return _rpc(rpc_id, error=_e(-31099, "Order is being paid by another transaction"))

    create_ms = _now_ms()
    await core.set_provider(payment.id, "payme")
    await db.execute(
        "UPDATE payments SET provider_txn_id=$1, payme_state=$2, payme_create_ms=$3 WHERE id=$4",
        txn_id, STATE_CREATED, create_ms, payment.id,
    )
    return _rpc(rpc_id, result={
        "create_time": create_ms, "transaction": str(payment.id), "state": STATE_CREATED})


async def _perform(rpc_id, params, bot):
    txn_id = str(params.get("id", ""))
    payment = await core.find_by_provider_txn("payme", txn_id)
    if not payment:
        return _rpc(rpc_id, error=_e(-31003, "Transaction not found"))

    pm = await _payme_times(payment.id)
    if payment.payme_state == STATE_DONE:
        return _rpc(rpc_id, result={
            "perform_time": pm["perform_ms"], "transaction": str(payment.id), "state": STATE_DONE})
    if payment.payme_state != STATE_CREATED:
        return _rpc(rpc_id, error=_e(-31008, "Cannot perform"))

    # 12-hour timeout: auto-cancel instead of performing.
    if _now_ms() - (pm["create_ms"] or 0) > TIMEOUT_MS:
        await core.payme_set_state(payment.id, STATE_CANCELLED, payme_cancel_ms=_now_ms())
        await core.mark_cancelled(payment.id, reason=4)
        return _rpc(rpc_id, error=_e(-31008, "Transaction timed out"))

    perform_ms = _now_ms()
    won = await core.mark_paid(payment.id, "payme", txn_id)
    await core.payme_set_state(payment.id, STATE_DONE, payme_perform_ms=perform_ms)
    if won:
        try:
            await core.fulfill(bot, await core.get_payment(payment.id))
        except Exception:
            logger.exception(f"[payme] fulfillment error #{payment.id}")
    return _rpc(rpc_id, result={
        "perform_time": perform_ms, "transaction": str(payment.id), "state": STATE_DONE})


async def _cancel(rpc_id, params):
    txn_id = str(params.get("id", ""))
    reason = params.get("reason")
    payment = await core.find_by_provider_txn("payme", txn_id)
    if not payment:
        return _rpc(rpc_id, error=_e(-31003, "Transaction not found"))

    pm = await _payme_times(payment.id)
    if payment.payme_state in (STATE_CANCELLED, STATE_CANCELLED_AFTER_DONE):
        return _rpc(rpc_id, result={
            "cancel_time": pm["cancel_ms"], "transaction": str(payment.id),
            "state": payment.payme_state})

    new_state = STATE_CANCELLED_AFTER_DONE if payment.payme_state == STATE_DONE else STATE_CANCELLED
    cancel_ms = _now_ms()
    await core.payme_set_state(payment.id, new_state, payme_cancel_ms=cancel_ms)
    await core.mark_cancelled(payment.id, reason=reason)
    # Note: digital goods already delivered aren't recalled; a refund is a manual decision.
    return _rpc(rpc_id, result={
        "cancel_time": cancel_ms, "transaction": str(payment.id), "state": new_state})


async def _check_txn(rpc_id, params):
    txn_id = str(params.get("id", ""))
    payment = await core.find_by_provider_txn("payme", txn_id)
    if not payment:
        return _rpc(rpc_id, error=_e(-31003, "Transaction not found"))
    pm = await _payme_times(payment.id)
    return _rpc(rpc_id, result={
        "create_time": pm["create_ms"] or 0,
        "perform_time": pm["perform_ms"] or 0,
        "cancel_time": pm["cancel_ms"] or 0,
        "transaction": str(payment.id),
        "state": payment.payme_state,
        "reason": pm["reason"],
    })


async def _statement(rpc_id, params):
    frm, to = params.get("from", 0), params.get("to", 0)
    rows = await db.fetch(
        "SELECT * FROM payments WHERE provider='payme' AND payme_create_ms BETWEEN $1 AND $2",
        frm, to,
    )
    txns = [{
        "id": r["provider_txn_id"],
        "time": r["payme_create_ms"],
        "amount": r["amount_tiyin"],
        "account": {"order_id": str(r["id"])},
        "create_time": r["payme_create_ms"] or 0,
        "perform_time": r["payme_perform_ms"] or 0,
        "cancel_time": r["payme_cancel_ms"] or 0,
        "transaction": str(r["id"]),
        "state": r["payme_state"],
        "reason": r["payme_reason"],
    } for r in rows]
    return _rpc(rpc_id, result={"transactions": txns})


async def _payme_times(payment_id: int) -> dict:
    r = await db.fetchrow(
        "SELECT payme_create_ms, payme_perform_ms, payme_cancel_ms, payme_reason "
        "FROM payments WHERE id=$1", payment_id)
    return {"create_ms": r["payme_create_ms"], "perform_ms": r["payme_perform_ms"],
            "cancel_ms": r["payme_cancel_ms"], "reason": r["payme_reason"]}
