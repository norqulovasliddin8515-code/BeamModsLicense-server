"""
core.py — one source of truth for every payment, across all providers.

Why a unified table: the old skeleton routed Click/Payme straight at the orders
table and re-derived "what was bought" from merchant_trans_id. Subscriptions
passed `sub_<uid>_<tier>` and crashed `int(merchant_trans_id)`; nothing checked
the paid amount; nothing was idempotent. Here every purchase — mod or
subscription, Click / Payme / Telegram Stars — is one `payments` row created
*before* the user is sent to the provider. The provider only ever references
that row's integer id, the amount is checked against it, and fulfillment runs
exactly once.

intent format (what is being bought):
    "mod:<mod_id>"      one-time mod purchase
    "sub:<tier>"        30-day subscription (tier = pro|max)

status lifecycle:
    pending  -> paid        (fulfilled: mod delivered / sub activated)
             -> cancelled   (provider cancelled / refunded)
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import aiosqlite

from bot.database import DB_PATH

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
    amount_tiyin: int           # canonical minor units (1 UZS = 100 tiyin)
    provider: str               # click | payme | stars  ('' until chosen)
    status: str                 # pending | paid | cancelled
    provider_txn_id: Optional[str]
    payme_state: int            # Payme transaction state (0/1/2/-1/-2)
    created_at: str
    paid_at: Optional[str]
    receipt_file_id: Optional[str] = None   # manual card-transfer receipt photo

    @property
    def amount_uzs(self) -> int:
        return self.amount_tiyin // 100

    def kind(self) -> str:
        return self.intent.split(":", 1)[0]

    def target(self) -> str:
        return self.intent.split(":", 1)[1]


async def init_payment_tables() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS payments (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL,
                intent          TEXT    NOT NULL,          -- mod:<id> | sub:<tier>
                amount_tiyin    INTEGER NOT NULL,          -- expected amount, in tiyin
                provider        TEXT    NOT NULL DEFAULT '',
                status          TEXT    NOT NULL DEFAULT 'pending',
                provider_txn_id TEXT,
                payme_state     INTEGER NOT NULL DEFAULT 0,
                payme_create_ms INTEGER,
                payme_perform_ms INTEGER,
                payme_cancel_ms INTEGER,
                payme_reason    INTEGER,
                created_at      TEXT    NOT NULL,
                paid_at         TEXT,
                fulfilled       INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id);
            CREATE INDEX IF NOT EXISTS idx_payments_txn  ON payments(provider, provider_txn_id);
            """
        )
        # Phase 3 manual-transfer columns + optional DRM key on mods (idempotent).
        await _add_column(db, "payments", "receipt_file_id", "TEXT")
        await _add_column(db, "payments", "admin_msg_chat", "INTEGER")
        await _add_column(db, "payments", "admin_msg_id", "INTEGER")
        await _add_column(db, "mods", "mod_key", "TEXT")
        await db.commit()


async def _add_column(db, table: str, col: str, decl: str) -> None:
    cur = await db.execute(f"PRAGMA table_info({table})")
    cols = [r[1] for r in await cur.fetchall()]
    if col not in cols:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def _row(r: aiosqlite.Row) -> Payment:
    return Payment(
        id=r["id"], user_id=r["user_id"], intent=r["intent"],
        amount_tiyin=r["amount_tiyin"], provider=r["provider"], status=r["status"],
        provider_txn_id=r["provider_txn_id"], payme_state=r["payme_state"],
        created_at=r["created_at"], paid_at=r["paid_at"],
        receipt_file_id=(r["receipt_file_id"] if "receipt_file_id" in r.keys() else None),
    )


async def create_payment(user_id: int, intent: str, amount_uzs: int, provider: str = "") -> Payment:
    if not INTENT_RE.match(intent):
        raise ValueError(f"bad intent: {intent!r}")
    if amount_uzs <= 0:
        raise ValueError("amount must be positive")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "INSERT INTO payments(user_id, intent, amount_tiyin, provider, created_at) "
            "VALUES (?,?,?,?,?)",
            (user_id, intent, amount_uzs * 100, provider, _now()),
        )
        await db.commit()
        pid = cur.lastrowid
        cur = await db.execute("SELECT * FROM payments WHERE id=?", (pid,))
        return _row(await cur.fetchone())


async def get_payment(payment_id: int) -> Optional[Payment]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM payments WHERE id=?", (payment_id,))
        r = await cur.fetchone()
        return _row(r) if r else None


async def set_provider(payment_id: int, provider: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE payments SET provider=? WHERE id=?", (provider, payment_id))
        await db.commit()


async def mark_paid(payment_id: int, provider: str, provider_txn_id: str) -> bool:
    """
    Flip pending -> paid atomically. Returns True only for the call that won
    the transition (so fulfillment runs exactly once, even if the provider
    retries the webhook).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "UPDATE payments SET status='paid', provider=?, provider_txn_id=?, paid_at=? "
            "WHERE id=? AND status='pending'",
            (provider, provider_txn_id, _now(), payment_id),
        )
        await db.commit()
        return cur.rowcount == 1


async def mark_cancelled(payment_id: int, reason: Optional[int] = None) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE payments SET status='cancelled', payme_reason=? WHERE id=?",
            (reason, payment_id),
        )
        await db.commit()


# ── Payme-specific state helpers ───────────────────────────────────────────
async def payme_set_state(payment_id: int, state: int, **times: int) -> None:
    cols = ", ".join(f"{k}=?" for k in times)
    sql = "UPDATE payments SET payme_state=?" + (", " + cols if cols else "") + " WHERE id=?"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(sql, (state, *times.values(), payment_id))
        await db.commit()


async def find_by_provider_txn(provider: str, txn_id: str) -> Optional[Payment]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM payments WHERE provider=? AND provider_txn_id=?", (provider, txn_id)
        )
        r = await cur.fetchone()
        return _row(r) if r else None


# ── Fulfillment ─────────────────────────────────────────────────────────────
async def fulfill(bot, payment: Payment) -> None:
    """
    Deliver what was paid for. Idempotent: guarded by the `fulfilled` flag so a
    provider that calls Perform twice (or Stars + webhook overlap) delivers once.
    Safe to call repeatedly.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "UPDATE payments SET fulfilled=1 WHERE id=? AND fulfilled=0", (payment.id,)
        )
        await db.commit()
        if cur.rowcount != 1:
            logger.info(f"[pay] #{payment.id} already fulfilled, skipping")
            return

    try:
        if payment.kind() == "mod":
            await _fulfill_mod(bot, payment)
        elif payment.kind() == "sub":
            await _fulfill_sub(bot, payment)
    except Exception:
        # Roll the flag back so a retry can try again rather than silently losing it.
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE payments SET fulfilled=0 WHERE id=?", (payment.id,))
            await db.commit()
        logger.exception(f"[pay] fulfillment failed for #{payment.id}")
        raise


async def _fulfill_mod(bot, payment: Payment) -> None:
    """
    Deliver a paid mod. If the mod has a DRM key configured (mods.mod_key set and
    a source in DRM_MODS_DIR, with a signing key present), build and send a
    per-buyer obfuscated bundle. Otherwise send the plain file. Stars and manual
    approval both reach this, so fulfillment is identical for both.
    """
    from bot.handlers import delivery
    mod_id = int(payment.target())
    await _record_order(payment, mod_id)
    if not await _try_deliver_drm(bot, payment, mod_id):
        await delivery.send_file_to_user(bot=bot, user_id=payment.user_id, mod_id=mod_id)
    logger.info(f"[pay] delivered mod {mod_id} to {payment.user_id} (payment #{payment.id})")


async def _mod_key(mod_id: int) -> Optional[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT mod_key FROM mods WHERE id=?", (mod_id,))
        r = await cur.fetchone()
    return (r["mod_key"] if r and r["mod_key"] else None)


async def _try_deliver_drm(bot, payment: Payment, mod_id: int) -> bool:
    """Returns True if a DRM bundle was built and sent; False to fall back to plain file."""
    import os
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
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO orders(user_id, mod_id, amount, payment_method, status, "
            "transaction_id, paid_at) VALUES (?,?,?,?, 'paid', ?, datetime('now'))",
            (payment.user_id, mod_id, payment.amount_uzs, payment.provider,
             payment.provider_txn_id or f"pay{payment.id}"),
        )
        await db.commit()


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
    """Click/Payme hosted-checkout URL. account/transaction_param = payment.id."""
    import base64 as _b64
    from bot.config import (
        CLICK_SERVICE_ID, CLICK_MERCHANT_ID, PAYME_MERCHANT_ID,
    )
    ret = "https://t.me/BeamModsStudio_bot"
    if provider == "click":
        return (
            "https://my.click.uz/services/pay"
            f"?service_id={CLICK_SERVICE_ID}&merchant_id={CLICK_MERCHANT_ID}"
            f"&amount={payment.amount_uzs}&transaction_param={payment.id}"
            f"&return_url={ret}"
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
    """Most recent card payment that is still waiting for the user's receipt."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM payments WHERE user_id=? AND provider='card' AND status=? "
            "ORDER BY id DESC LIMIT 1",
            (user_id, STATUS_PENDING),
        )
        r = await cur.fetchone()
        return _row(r) if r else None


async def attach_receipt(payment_id: int, file_id: str) -> bool:
    """pending -> pending_manual_verification, storing the receipt photo file_id."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "UPDATE payments SET status=?, receipt_file_id=? WHERE id=? AND status=?",
            (STATUS_AWAIT_VERIFY, file_id, payment_id, STATUS_PENDING),
        )
        await db.commit()
        return cur.rowcount == 1


async def set_admin_msg(payment_id: int, chat_id: int, msg_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE payments SET admin_msg_chat=?, admin_msg_id=? WHERE id=?",
            (chat_id, msg_id, payment_id),
        )
        await db.commit()


async def approve_manual(payment_id: int) -> bool:
    """pending_manual_verification -> paid, atomically. True only for the winning call."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "UPDATE payments SET status='paid', provider='card', paid_at=? "
            "WHERE id=? AND status=?",
            (_now(), payment_id, STATUS_AWAIT_VERIFY),
        )
        await db.commit()
        return cur.rowcount == 1


async def reject_manual(payment_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "UPDATE payments SET status='rejected' WHERE id=? AND status=?",
            (payment_id, STATUS_AWAIT_VERIFY),
        )
        await db.commit()
        return cur.rowcount == 1
