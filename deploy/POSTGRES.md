# Phase 4 — SQLite → PostgreSQL migration

The whole data layer now runs on PostgreSQL via `asyncpg`, through one shared
connection pool (`bot/db.py`). SQLite is gone from the app; it's used only by
the one-time transfer script.

## What changed

| File | Change |
|---|---|
| `bot/db.py` **(new)** | asyncpg pool + helpers (`fetch`/`fetchrow`/`fetchval`/`execute`/`execute_count`/`transaction`). Native `$1` placeholders. |
| `bot/database.py` | ported: `SERIAL` ids, `INSERT … RETURNING id`, `= ANY($1)`, ISO-text timestamps. Added the missing `has_user_purchased` / `get_user_purchases` aliases the handlers were calling (previously an AttributeError), plus `set_mod_key` / `set_mod_image` / `increment_downloads`. |
| `bot/drm/licenses.py` | ported; `check_and_bind` now runs in a real transaction with `SELECT … FOR UPDATE`, so the first-bind race is airtight. |
| `bot/payments/core.py`, `payme.py` | ported; same idempotency and atomic paid-flip, now on Postgres. |
| `bot/handlers/{admin,delivery,drm_admin}.py`, `bot/main.py` | the few direct `aiosqlite.connect()` spots now call pool helpers. |
| `bot/main.py` | opens the pool at startup (before any query) and closes it on shutdown. |
| `scripts/migrate_sqlite_to_pg.py` **(new)** | one-time data transfer. |
| `requirements.txt` | `asyncpg==0.30.0` added (aiosqlite kept only for the transfer script). |

Timestamps are stored as ISO-8601 UTC **text**, exactly as the code already
produced them — so no datetime-type surprises and the transfer copies them verbatim.

## Deploy steps (on the VPS)

```bash
cd /opt/beammods/app
source /opt/beammods/venv/bin/activate
git pull            # or re-upload the changed files
pip install -r bot/requirements.txt      # installs asyncpg

# .env must contain DATABASE_URL (from the Phase-4 DB setup):
#   DATABASE_URL=postgresql://beammods:YOURPASS@127.0.0.1:5432/beammods
```

### Migrate your existing data

Stop the bot first so nothing writes during the copy:

```bash
sudo systemctl stop beammods
python scripts/migrate_sqlite_to_pg.py --sqlite beamods.db
```

Expected output — one line per table with matching counts:

```
  users            — N in SQLite -> N in PG …
  mods             — N in SQLite -> N in PG …
  orders / licenses / license_events / payments …
```

The script is **safe to re-run**: it does a clean import (truncates each table
first, pass `--keep` to append instead), copies only columns present in both
databases, and resets the `SERIAL` sequences so new inserts never collide with
imported ids. It opens `beamods.db` read-only and never seeds demo rows.

Then start the bot and spot-check:

```bash
sudo systemctl start beammods
journalctl -u beammods -f      # expect "Database ready (PostgreSQL)."
```

- `/start`, open the Mini App catalog (mods load from PG)
- `/plan` and `/myorders` for a user who had data in SQLite
- a small card-transfer purchase end to end

Keep `beamods.db` as a backup until you've confirmed a few days of clean operation.

## Tests (need a throwaway PostgreSQL)

Create a scratch DB and point `TEST_DATABASE_URL` at it (the suites DROP/recreate
the `public` schema, so never aim this at production):

```bash
sudo -u postgres psql -c "CREATE DATABASE beammods_test OWNER beammods;"
export TEST_DATABASE_URL="postgresql://beammods:YOURPASS@127.0.0.1:5432/beammods_test"

python tests/db/test_pg_port.py        # 32 checks: migration + every ported query
python tests/payments/test_payments.py # 27 checks: Stars/Click/Payme on PG
python tests/payments/test_manual.py   # 20 checks: card flow + DRM delivery on PG
```

> The schema reset needs the `beammods` role to **own** the test DB (PG 15+ locks
> down the `public` schema otherwise) — the `OWNER beammods` above handles it.

## Note on `tests/drm/e2e.py`

That DRM end-to-end test also uses the licenses table. It now needs the pool: set
`DATABASE_URL` and add `await bot.db.init_pool()` at its start (and a schema reset
like the other suites). Say the word and I'll update it — I couldn't see it in my
working copy this session.

## Rollback

The app reads its backend from `DATABASE_URL`. If anything misbehaves, the old
SQLite build is one `git`/file revert away, and `beamods.db` is untouched by the
migration. Nothing in the transfer is destructive to the source.
