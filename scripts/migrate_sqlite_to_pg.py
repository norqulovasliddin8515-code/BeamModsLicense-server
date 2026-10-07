"""
migrate_sqlite_to_pg.py — one-time copy of the SQLite data into PostgreSQL.

Usage (on the VPS, from the project root, venv active, DATABASE_URL set):

    python scripts/migrate_sqlite_to_pg.py --sqlite beamods.db
    python scripts/migrate_sqlite_to_pg.py --sqlite beamods.db --truncate   # wipe PG tables first

What it does, safely:
  1. ensures the PostgreSQL schema exists (runs the app's own init_* funcs)
  2. for each table, copies only the columns that exist in BOTH databases
     (so an older SQLite file missing newer columns still migrates)
  3. inserts with ON CONFLICT (pk) DO NOTHING — re-running never duplicates
  4. resets each SERIAL sequence to MAX(id) so new inserts don't collide
  5. prints row counts and verifies SQLite vs PostgreSQL afterwards

It never modifies the SQLite file (opened read-only).
"""
import argparse
import asyncio
import os
import sqlite3
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bot import db as dbpool
from bot import database
from bot.drm import licenses as drm_licenses
from bot.payments import core as pay_core

# table -> (primary key column, is SERIAL id needing sequence reset)
TABLES = [
    ("users", "id", False),
    ("mods", "id", True),
    ("orders", "id", True),
    ("licenses", "token", False),
    ("license_events", "id", True),
    ("payments", "id", True),
]


def sqlite_columns(scon, table):
    cur = scon.execute(f"PRAGMA table_info({table})")
    return [r[1] for r in cur.fetchall()]


def sqlite_has_table(scon, table):
    cur = scon.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


async def pg_columns(table):
    rows = await dbpool.fetch(
        "SELECT column_name FROM information_schema.columns WHERE table_name=$1", table)
    return [r["column_name"] for r in rows]


async def migrate_table(scon, table, pk, is_serial, truncate):
    if not sqlite_has_table(scon, table):
        print(f"  {table:16s} — not in SQLite, skipped")
        return
    scols = sqlite_columns(scon, table)
    pcols = await pg_columns(table)
    cols = [c for c in scols if c in pcols]          # intersection, SQLite order
    if not cols:
        print(f"  {table:16s} — no common columns, skipped")
        return

    rows = scon.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
    if truncate:
        await dbpool.execute(f"TRUNCATE {table} RESTART IDENTITY CASCADE")

    if rows:
        placeholders = ", ".join(f"${i}" for i in range(1, len(cols) + 1))
        sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
               f"ON CONFLICT ({pk}) DO NOTHING")
        async with dbpool.transaction() as con:
            await con.executemany(sql, [tuple(r) for r in rows])

    if is_serial:
        # Point the sequence past the largest imported id.
        await dbpool.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {table}), 1), (SELECT COUNT(*) FROM {table}) > 0)")

    src = len(rows)
    dst = await dbpool.fetchval(f"SELECT COUNT(*) FROM {table}")
    flag = "" if dst >= src else "  ⚠️ fewer rows in PG!"
    print(f"  {table:16s} — {src} in SQLite -> {dst} in PG (copied {len(cols)} cols){flag}")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", default=database.DB_PATH, help="path to beamods.db")
    ap.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    ap.add_argument("--keep", action="store_true",
                    help="do NOT empty PG tables first (default is a clean truncate import)")
    a = ap.parse_args()
    if not a.database_url:
        raise SystemExit("Set DATABASE_URL (or pass --database-url)")
    if not os.path.exists(a.sqlite):
        raise SystemExit(f"SQLite file not found: {a.sqlite}")

    await dbpool.init_pool(a.database_url)
    print("Ensuring PostgreSQL schema…")
    await database.init_db(seed=False)   # never seed demo rows into a real import
    await drm_licenses.init_drm_tables()
    await pay_core.init_payment_tables()

    scon = sqlite3.connect(f"file:{a.sqlite}?mode=ro", uri=True)
    try:
        print(f"Migrating {a.sqlite} → PostgreSQL" + ("  (clean import)" if not a.keep else "  (keep existing)"))
        truncate = not a.keep
        for table, pk, is_serial in TABLES:
            await migrate_table(scon, table, pk, is_serial, truncate)
    finally:
        scon.close()
        await dbpool.close_pool()
    print("Done. Spot-check the bot, then switch RUN away from SQLite.")


if __name__ == "__main__":
    asyncio.run(main())
