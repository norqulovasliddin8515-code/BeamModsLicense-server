"""
core.py — one source of truth for every payment, across all providers.

Every purchase — mod or subscription, Click / Payme / Telegram Stars / manual
card transfer — is one `payments` row created *before* the user is sent to the
provider. The provider only ever references that row's integer id, the amount is
checked against it, and fulfillment runs exactly once.

intent:  "mod:<mod_id>"  |  "sub:<tier>"  (tier = pro|max)
status:  pending -> pending_manual_verification -> paid | rejected | cancelled

PostgreSQL / asyncpg (Phase 4). Shares the bot.db pool.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from bot import db

logger = logging.getLogger(__name__)

INTENT_RE = re.compile(r"^(mod:\d+|sub:(pro|max))$")
SUB_DAYS = 30


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Payment:
    id: int
    user_id: int
    intent: str
    amount_tiyin: int
    provider: str
    status: str
    provider_txn_id: Optional[str]
    payme_state: int
    created_at: str
    paid_at: Optional[str]
    receipt_file_id: Optional[str] = None

    @property
    def amount_uzs(self) -> int:
        return self.amount_tiyin // 100

    def kind(self) -> str:
        return self.intent.split(":", 1)[0]

    def target(self) -> str:
        return self.intent.split(":", 1)[1]


async def init_payment_tables() -> None:
    await db.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id               SERIAL PRIMARY KEY,
            user_id          BIGINT NOT NULL,
            intent           TEXT   NOT NULL,
            amount_tiyin     BIGINT NOT NULL,
            provider         TEXT   NOT NULL DEFAULT '',
            status           TEXT   NOT NULL DEFAULT 'pending',
            provider_txn_id  TEXT,
            payme_state      INTEGER NOT NULL DEFAULT 0,
            payme_create_ms  BIGINT,
            payme_perform_ms BIGINT,
            payme_cancel_ms  BIGINT,
            payme_reason     INTEGER,
            created_at       TEXT   NOT NULL,
            paid_at          TEXT,
            fulfilled        INTEGER NOT NULL DEFAULT 0,
            receipt_file_id  TEXT,
            admin_msg_chat   BIGINT,
            admin_msg_id     BIGINT
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id)")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_payments_txn ON payments(provider, provider_txn_id)")
    # DRM flag on mods (also ensured in database.init_db; harmless if already there).
    await db.execute("ALTER TABLE mods ADD COLUMN IF NOT EXISTS mod_key TEXT")


def _row(r) -> Payment:
    return Payment(
        id=r["id"], user_id=r["user_id"], intent=r["intent"],
        amount_tiyin=r["amount_tiyin"], provider=r["provider"], status=r["status"],
        provider_txn_id=r["provider_txn_id"], payme_state=r["payme_state"],
        created_at=r["created_at"], paid_at=r["paid_at"],
        receipt_file_id=r["receipt_file_id"],
    )


async def create_payment(user_id: int, intent: str, amount_uzs: int, provider: str = "") -> Payment:
    if not INTENT_RE.match(intent):
        raise ValueError(f"bad intent: {intent!r}")
    if amount_uzs <= 0:
        raise ValueError("amount must be positive")
    r = await db.fetchrow(
        "INSERT INTO payments(user_id, intent, amount_tiyin, provider, created_at) "
        "VALUES ($1,$2,$3,$4,$5) RETURNING *",
        user_id, intent, amount_uzs * 100, provider, _now(),
    )
    return _row(r)


async def get_payment(payment_id: int) -> Optional[Payment]:
    r = await db.fetchrow("SELECT * FROM payments WHERE id=$1", payment_id)
    return _row(r) if r else None


async def set_provider(payment_id: int, provider: str) -> None:
    await db.execute("UPDATE payments SET provider=$1 WHERE id=$2", provider, payment_id)


async def mark_paid(payment_id: int, provider: str, provider_txn_id: str) -> bool:
    """Flip pending -> paid atomically. True only for the winning call."""
    return await db.execute_count(
        "UPDATE payments SET status='paid', provider=$1, provider_txn_id=$2, paid_at=$3 "
        "WHERE id=$4 AND status='pending'",
        provider, provider_txn_id, _now(), payment_id,
    ) == 1


async def mark_cancelled(payment_id: int, reason: Optional[int] = None) -> None:
    await db.execute(
        "UPDATE payments SET status='cancelled', payme_reason=$1 WHERE id=$2", reason, payment_id)


# ── Payme-specific state helpers ───────────────────────────────────────────
async def payme_set_state(payment_id: int, state: int, **times: int) -> None:
    sets = ["payme_state=$1"]
    args = [state]
    for i, (k, v) in enumerate(times.items(), start=2):
        sets.append(f"{k}=${i}")
        args.append(v)
    args.append(payment_id)
    sql = f"UPDATE payments SET {', '.join(sets)} WHERE id=${len(args)}"
    await db.execute(sql, *args)


async def find_by_provider_txn(provider: str, txn_id: str) -> Optional[Payment]:
    r = await db.fetchrow(
        "SELECT * FROM payments WHERE provider=$1 AND provider_txn_id=$2", provider, txn_id)
    return _row(r) if r else None


# ── Fulfillment ─────────────────────────────────────────────────────────────
async def fulfill(bot, payment: Payment) -> None:
    """Deliver what was paid for. Idempotent via the `fulfilled` flag."""
    won = await db.execute_count(
        "UPDATE payments SET fulfilled=1 WHERE id=$1 AND fulfilled=0", payment.id)
    if won != 1:
        logger.info(f"[pay] #{payment.id} already fulfilled, skipping")
        return
    try:
        if payment.kind() == "mod":
            await _fulfill_mod(bot, payment)
        elif payment.kind() == "sub":
            await _fulfill_sub(bot, payment)
    except Exception:
        await db.execute("UPDATE payments SET fulfilled=0 WHERE id=$1", payment.id)
        logger.exception(f"[pay] fulfillment failed for #{payment.id}")
        raise


async def _fulfill_mod(bot, payment: Payment) -> None:
    from bot.handlers import delivery
    mod_id = int(payment.target())
    await _record_order(payment, mod_id)
    if not await _try_deliver_drm(bot, payment, mod_id):
        await delivery.send_file_to_user(bot=bot, user_id=payment.user_id, mod_id=mod_id)
    logger.info(f"[pay] delivered mod {mod_id} to {payment.user_id} (payment #{payment.id})")


async def _mod_key(mod_id: int) -> Optional[str]:
    return await db.fetchval("SELECT mod_key FROM mods WHERE id=$1", mod_id) or None


async def _try_deliver_drm(bot, payment: Payment, mod_id: int) -> bool:
    """True if a DRM bundle was built and sent; False to fall back to plain file."""
    from pathlib import Path
    from bot.config import DRM_SIGNING_KEY_FILE, DRM_MODS_DIR, PUBLIC_BASE_URL

    mod_key = await _mod_key(mod_id)
    if not (mod_key and DRM_SIGNING_KEY_FILE and DRM_MODS_DIR and PUBLIC_BASE_URL):
        return False
    source = Path(DRM_MODS_DIR) / mod_key
    if not source.is_dir():
        logger.warning(f"[pay] DRM source missing for {mod_key}; plain delivery")
        return False
    try:
        import tempfile
        from aiogram.types import FSInputFile
        from bot.drm.keys import get_public_hex
        from bot.drm import licenses
        from bot.drm.bundler import build_bundle_async

        await licenses.init_drm_tables()
        token = await licenses.issue_license(payment.user_id, mod_key, note=f"payment#{payment.id}")
        with tempfile.TemporaryDirectory(prefix="bms_pay_drm_") as tmp:
            zip_path = Path(tmp) / f"{mod_key}.zip"
            await build_bundle_async(source, zip_path, token=token, mod_key=mod_key,
                                     pubkey_hex=get_public_hex(), base_url=PUBLIC_BASE_URL)
            await bot.send_document(
                chat_id=payment.user_id,
                document=FSInputFile(zip_path, filename=f"{mod_key}.zip"),
                caption=(
                    "✅ <b>Xaridingiz uchun rahmat!</b>\n\n"
                    "Ushbu nusxa shaxsiy litsenziyangizga bog'langan. "
                    "Birinchi ishga tushirishda internet kerak bo'ladi."
                ),
            )
        logger.info(f"[pay] DRM bundle sent mod_key={mod_key} to {payment.user_id}")
        return True
    except Exception:
        logger.exception(f"[pay] DRM build failed for {mod_key}; falling back to plain file")
        return False


async def _fulfill_sub(bot, payment: Payment) -> None:
    from bot import database as db_mod
    tier = payment.target()
    expire = await db_mod.upgrade_subscription(payment.user_id, tier, days=SUB_DAYS)
    try:
        info = db_mod.TIERS.get(tier, {})
        await bot.send_message(
            payment.user_id,
            f"{info.get('emoji', '')} <b>{info.get('name', tier.upper())}</b> "
            f"obunangiz faollashtirildi!\nAmal qiladi: <b>{expire}</b> gacha.\n\nRahmat! 🎉",
        )
    except Exception as e:
        logger.warning(f"[pay] sub confirm message failed: {e}")
    logger.info(f"[pay] activated {tier} for {payment.user_id} until {expire} (payment #{payment.id})")


async def _record_order(payment: Payment, mod_id: int) -> None:
    """Mirror the purchase into the legacy `orders` table so /myorders keeps working."""
    await db.execute(
        "INSERT INTO orders(user_id, mod_id, amount, payment_method, status, transaction_id, paid_at) "
        "VALUES ($1,$2,$3,$4,'paid',$5,$6)",
        payment.user_id, mod_id, payment.amount_uzs, payment.provider,
        payment.provider_txn_id or f"pay{payment.id}", _now(),
    )


# ── Pricing (single source) ────────────────────────────────────────────────
async def price_for_intent(intent: str) -> int:
    """Authoritative price in UZS. Never trust an amount supplied by the client."""
    from bot import database as db_mod
    kind, target = intent.split(":", 1)
    if kind == "sub":
        return int(db_mod.TIERS[target]["price_uzs"])
    if kind == "mod":
        mod = await db_mod.get_mod_by_id(int(target))
        if not mod:
            raise ValueError("mod not found")
        return int(mod["price"])
    raise ValueError("bad intent")


# ── Provider checkout URLs ─────────────────────────────────────────────────
def build_checkout_url(provider: str, payment: "Payment") -> str:
    import base64 as _b64
    from bot.config import CLICK_SERVICE_ID, CLICK_MERCHANT_ID, PAYME_MERCHANT_ID
    ret = "https://t.me/BeamModsStudio_bot"
    if provider == "click":
        return (
            "https://my.click.uz/services/pay"
            f"?service_id={CLICK_SERVICE_ID}&merchant_id={CLICK_MERCHANT_ID}"
            f"&amount={payment.amount_uzs}&transaction_param={payment.id}&return_url={ret}"
        )
    if provider == "payme":
        params = f"m={PAYME_MERCHANT_ID};ac.order_id={payment.id};a={payment.amount_tiyin};l=uz;c={ret}"
        return "https://checkout.paycom.uz/" + _b64.b64encode(params.encode()).decode()
    return ret


# ── Manual card-transfer flow ───────────────────────────────────────────────
STATUS_PENDING = "pending"
STATUS_AWAIT_VERIFY = "pending_manual_verification"
STATUS_PAID = "paid"
STATUS_REJECTED = "rejected"
STATUS_CANCELLED = "cancelled"


async def latest_awaiting_receipt(user_id: int) -> Optional[Payment]:
    r = await db.fetchrow(
        "SELECT * FROM payments WHERE user_id=$1 AND provider='card' AND status=$2 "
        "ORDER BY id DESC LIMIT 1",
        user_id, STATUS_PENDING,
    )
    return _row(r) if r else None


async def attach_receipt(payment_id: int, file_id: str) -> bool:
    return await db.execute_count(
        "UPDATE payments SET status=$1, receipt_file_id=$2 WHERE id=$3 AND status=$4",
        STATUS_AWAIT_VERIFY, file_id, payment_id, STATUS_PENDING,
    ) == 1


async def set_admin_msg(payment_id: int, chat_id: int, msg_id: int) -> None:
    await db.execute(
        "UPDATE payments SET admin_msg_chat=$1, admin_msg_id=$2 WHERE id=$3",
        chat_id, msg_id, payment_id,
    )


async def approve_manual(payment_id: int) -> bool:
    """pending_manual_verification -> paid, atomically. True only for the winning call."""
    return await db.execute_count(
        "UPDATE payments SET status='paid', provider='card', paid_at=$1 WHERE id=$2 AND status=$3",
        _now(), payment_id, STATUS_AWAIT_VERIFY,
    ) == 1


async def reject_manual(payment_id: int) -> bool:
    return await db.execute_count(
        "UPDATE payments SET status='rejected' WHERE id=$1 AND status=$2",
        payment_id, STATUS_AWAIT_VERIFY,
    ) == 1
