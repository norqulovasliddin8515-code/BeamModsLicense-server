"""
licenses.py — license storage (SQLite now, PostgreSQL in Phase 4).

One row per sold bundle. The token is embedded (encrypted) inside the buyer's
personal zip and doubles as the leak watermark: a leaked zip names its buyer.
"""
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import aiosqlite

from bot.database import DB_PATH

TOKEN_RE = re.compile(r"^bms_[A-Za-z0-9_-]{24,64}$")
MOD_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def init_drm_tables() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS licenses (
                token           TEXT PRIMARY KEY,
                user_id         INTEGER NOT NULL,
                mod_key         TEXT    NOT NULL,
                hwid            TEXT,                       -- bound on first /verify
                status          TEXT    NOT NULL DEFAULT 'active',  -- active | revoked
                created_at      TEXT    NOT NULL,
                bound_at        TEXT,
                last_seen_at    TEXT,
                last_ip         TEXT,
                verify_count    INTEGER NOT NULL DEFAULT 0,
                mismatch_count  INTEGER NOT NULL DEFAULT 0,
                note            TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_licenses_user ON licenses(user_id);
            CREATE INDEX IF NOT EXISTS idx_licenses_mod  ON licenses(mod_key);

            CREATE TABLE IF NOT EXISTS license_events (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                token   TEXT NOT NULL,
                event   TEXT NOT NULL,      -- bind | ok | hwid_mismatch | revoked | wrong_mod | reset
                hwid    TEXT,
                ip      TEXT,
                at      TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_license_events_token ON license_events(token);
            """
        )
        await db.commit()


async def issue_license(user_id: int, mod_key: str, note: str = "") -> str:
    if not MOD_KEY_RE.match(mod_key):
        raise ValueError("bad mod_key")
    token = "bms_" + secrets.token_urlsafe(24)   # 192-bit, unguessable
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO licenses(token, user_id, mod_key, created_at, note) VALUES (?,?,?,?,?)",
            (token, user_id, mod_key, _now(), note),
        )
        await db.commit()
    return token


async def get_license(token: str) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM licenses WHERE token=?", (token,))
        row = await cur.fetchone()
    return dict(row) if row else None


async def check_and_bind(token: str, mod_key: str, hwid: str, ip: str) -> str:
    """
    Returns one of: ok | invalid | wrong_mod | revoked | hwid_mismatch
    First successful call binds the token to `hwid` atomically.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM licenses WHERE token=?", (token,))
        lic = await cur.fetchone()
        if not lic:
            return "invalid"

        if lic["mod_key"] != mod_key:
            status = "wrong_mod"
        elif lic["status"] != "active":
            status = "revoked"
        elif lic["hwid"] is None:
            # Atomic first bind: only one concurrent request can win.
            cur = await db.execute(
                "UPDATE licenses SET hwid=?, bound_at=? WHERE token=? AND hwid IS NULL",
                (hwid, _now(), token),
            )
            if cur.rowcount == 1:
                await _event(db, token, "bind", hwid, ip)
                status = "ok"
            else:
                cur = await db.execute("SELECT hwid FROM licenses WHERE token=?", (token,))
                status = "ok" if (await cur.fetchone())[0] == hwid else "hwid_mismatch"
        elif lic["hwid"] == hwid:
            status = "ok"
        else:
            status = "hwid_mismatch"

        if status == "ok":
            await db.execute(
                "UPDATE licenses SET last_seen_at=?, last_ip=?, verify_count=verify_count+1 WHERE token=?",
                (_now(), ip, token),
            )
        else:
            if status == "hwid_mismatch":
                await db.execute(
                    "UPDATE licenses SET mismatch_count=mismatch_count+1 WHERE token=?", (token,)
                )
            await _event(db, token, status, hwid, ip)
        await db.commit()
        return status


async def reset_hwid(token: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "UPDATE licenses SET hwid=NULL, bound_at=NULL WHERE token=?", (token,)
        )
        if cur.rowcount:
            await _event(db, token, "reset", None, None)
        await db.commit()
        return cur.rowcount == 1


async def set_status(token: str, status: str) -> bool:
    assert status in ("active", "revoked")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("UPDATE licenses SET status=? WHERE token=?", (status, token))
        await db.commit()
        return cur.rowcount == 1


async def _event(db, token: str, event: str, hwid: Optional[str], ip: Optional[str]) -> None:
    await db.execute(
        "INSERT INTO license_events(token, event, hwid, ip, at) VALUES (?,?,?,?,?)",
        (token, event, hwid, ip, _now()),
    )
