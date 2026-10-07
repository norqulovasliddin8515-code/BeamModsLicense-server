"""
licenses.py — license storage (PostgreSQL / asyncpg, Phase 4).

One row per sold bundle. The token is embedded (encrypted) inside the buyer's
personal zip and doubles as the leak watermark: a leaked zip names its buyer.
"""
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from bot import db

TOKEN_RE = re.compile(r"^bms_[A-Za-z0-9_-]{24,64}$")
MOD_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def init_drm_tables() -> None:
    await db.execute("""
        CREATE TABLE IF NOT EXISTS licenses (
            token           TEXT PRIMARY KEY,
            user_id         BIGINT NOT NULL,
            mod_key         TEXT   NOT NULL,
            hwid            TEXT,
            status          TEXT   NOT NULL DEFAULT 'active',   -- active | revoked
            created_at      TEXT   NOT NULL,
            bound_at        TEXT,
            last_seen_at    TEXT,
            last_ip         TEXT,
            verify_count    BIGINT NOT NULL DEFAULT 0,
            mismatch_count  BIGINT NOT NULL DEFAULT 0,
            note            TEXT
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_licenses_user ON licenses(user_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_licenses_mod  ON licenses(mod_key)")
    await db.execute("""
        CREATE TABLE IF NOT EXISTS license_events (
            id      SERIAL PRIMARY KEY,
            token   TEXT NOT NULL,
            event   TEXT NOT NULL,
            hwid    TEXT,
            ip      TEXT,
            at      TEXT NOT NULL
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_license_events_token ON license_events(token)")


async def issue_license(user_id: int, mod_key: str, note: str = "") -> str:
    if not MOD_KEY_RE.match(mod_key):
        raise ValueError("bad mod_key")
    token = "bms_" + secrets.token_urlsafe(24)
    await db.execute(
        "INSERT INTO licenses(token, user_id, mod_key, created_at, note) VALUES ($1,$2,$3,$4,$5)",
        token, user_id, mod_key, _now(), note,
    )
    return token


async def get_license(token: str) -> Optional[Dict[str, Any]]:
    row = await db.fetchrow("SELECT * FROM licenses WHERE token=$1", token)
    return dict(row) if row else None


async def check_and_bind(token: str, mod_key: str, hwid: str, ip: str) -> str:
    """
    Returns: ok | invalid | wrong_mod | revoked | hwid_mismatch
    First successful call binds the token to `hwid`. The bind + counters run in
    one transaction on a single connection; the conditional UPDATE (… AND hwid
    IS NULL) makes the first-bind race-safe.
    """
    async with db.transaction() as con:
        lic = await con.fetchrow("SELECT * FROM licenses WHERE token=$1 FOR UPDATE", token)
        if not lic:
            return "invalid"

        if lic["mod_key"] != mod_key:
            status = "wrong_mod"
        elif lic["status"] != "active":
            status = "revoked"
        elif lic["hwid"] is None:
            tag = await con.execute(
                "UPDATE licenses SET hwid=$1, bound_at=$2 WHERE token=$3 AND hwid IS NULL",
                hwid, _now(), token,
            )
            if db.tag_count(tag) == 1:
                await _event(con, token, "bind", hwid, ip)
                status = "ok"
            else:
                cur_hwid = await con.fetchval("SELECT hwid FROM licenses WHERE token=$1", token)
                status = "ok" if cur_hwid == hwid else "hwid_mismatch"
        elif lic["hwid"] == hwid:
            status = "ok"
        else:
            status = "hwid_mismatch"

        if status == "ok":
            await con.execute(
                "UPDATE licenses SET last_seen_at=$1, last_ip=$2, verify_count=verify_count+1 "
                "WHERE token=$3", _now(), ip, token,
            )
        else:
            if status == "hwid_mismatch":
                await con.execute(
                    "UPDATE licenses SET mismatch_count=mismatch_count+1 WHERE token=$1", token)
            await _event(con, token, status, hwid, ip)
        return status


async def reset_hwid(token: str) -> bool:
    async with db.transaction() as con:
        tag = await con.execute(
            "UPDATE licenses SET hwid=NULL, bound_at=NULL WHERE token=$1", token)
        if db.tag_count(tag):
            await _event(con, token, "reset", None, None)
        return db.tag_count(tag) == 1


async def set_status(token: str, status: str) -> bool:
    assert status in ("active", "revoked")
    return await db.execute_count(
        "UPDATE licenses SET status=$1 WHERE token=$2", status, token) == 1


async def _event(con, token: str, event: str, hwid: Optional[str], ip: Optional[str]) -> None:
    await con.execute(
        "INSERT INTO license_events(token, event, hwid, ip, at) VALUES ($1,$2,$3,$4,$5)",
        token, event, hwid, ip, _now(),
    )
