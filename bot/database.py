"""
database.py — BeamModsStudio async data layer (PostgreSQL / asyncpg, Phase 4).

Tables: users, mods, orders. (licenses/license_events live in bot.drm.licenses,
payments in bot.payments.core — all share the bot.db pool.)

Ported from the old aiosqlite layer:
  • SERIAL ids instead of AUTOINCREMENT
  • $1 placeholders instead of ?
  • INSERT ... RETURNING id instead of cursor.lastrowid
  • now-ISO text defaults instead of datetime('now')
  • ON CONFLICT unchanged (Postgres supports it)
DB_PATH is kept only for the one-time SQLite→PG transfer script.
"""
from datetime import date, datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

from bot import db

DB_PATH = "beamods.db"   # legacy SQLite file, used only by the migration script

# ISO-8601 UTC text, matches the representation used elsewhere (licenses/payments).
_NOW_SQL = "to_char((now() at time zone 'utc'), 'YYYY-MM-DD\"T\"HH24:MI:SS+00:00')"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ═══════════════════════════════════════════════════════════════
#  INIT
# ═══════════════════════════════════════════════════════════════
async def init_db(seed: bool = True) -> None:
    """Create tables (idempotent). Seeds demo mods on an empty catalog unless seed=False
    (the migration passes seed=False so it never collides with imported ids)."""
    await db.execute(f"""
        CREATE TABLE IF NOT EXISTS users (
            id                   BIGINT PRIMARY KEY,          -- Telegram user_id
            name                 TEXT   NOT NULL,
            username             TEXT,
            joined_at            TEXT   DEFAULT {_NOW_SQL},
            subscription_tier    TEXT   DEFAULT 'free',
            subscription_expire  TEXT                          -- ISO date, NULL = free
        )
    """)
    await db.execute(f"""
        CREATE TABLE IF NOT EXISTS mods (
            id          SERIAL PRIMARY KEY,
            name        TEXT    NOT NULL,
            category    TEXT    NOT NULL,
            description TEXT,
            price       BIGINT  NOT NULL DEFAULT 0,
            image_url   TEXT,
            video_url   TEXT,
            file_id     TEXT    NOT NULL,
            downloads   BIGINT  DEFAULT 0,
            created_at  TEXT    DEFAULT {_NOW_SQL},
            mod_key     TEXT                                   -- set => DRM-protected
        )
    """)
    await db.execute(f"""
        CREATE TABLE IF NOT EXISTS orders (
            id             SERIAL PRIMARY KEY,
            user_id        BIGINT  NOT NULL,
            mod_id         BIGINT  NOT NULL,
            amount         BIGINT  NOT NULL,
            payment_method TEXT    DEFAULT 'click',
            status         TEXT    DEFAULT 'pending',
            transaction_id TEXT,
            created_at     TEXT    DEFAULT {_NOW_SQL},
            paid_at        TEXT
        )
    """)
    # mod_key may be missing on an older mods table carried over by the migration.
    await db.execute("ALTER TABLE mods ADD COLUMN IF NOT EXISTS mod_key TEXT")

    if seed and await db.fetchval("SELECT COUNT(*) FROM mods") == 0:
        await _seed_demo_mods()
    print("[OK] Database ready (PostgreSQL).")


async def _seed_demo_mods() -> None:
    demo = [
        ("BMW M5 F90 Competition", "cars", "Haqiqiy PBR teksturalar, V8 motor ovozlari bilan.",
         49900, "https://images.unsplash.com/photo-1555215695-3004980ad54e?w=800",
         "https://www.youtube.com/shorts/dQw4w9WgXcQ", "demo_file_bmw"),
        ("Kamaz 6520 Offroad", "trucks", "Og'ir offroad yuk mashinasi — sakrash fizikasi.",
         34900, "https://images.unsplash.com/photo-1601584115197-04ecc0da31d7?w=800",
         "https://www.youtube.com/shorts/9bZkp7q19f0", "demo_file_kamaz"),
        ("Toshkent Shahri Xaritasi", "maps", "20+ km² ochiq dunyo, tungi chiroqlar.",
         24900, "https://images.unsplash.com/photo-1578895101408-1a36b834405b?w=800",
         "https://www.youtube.com/shorts/CevxZvSJLk8", "demo_file_tashkent"),
        ("Nissan GT-R R35 Nismo", "cars", "600 HP drift spec, Launch Control, sport egzoz.",
         59900, "https://images.unsplash.com/photo-1617788138017-80ad40651399?w=800",
         "https://www.youtube.com/shorts/kffacxfA7G4", "demo_file_gtr"),
        ("BeamNG Flatbed Truck Pro", "trucks", "Offroad uchun mo'ljallangan keng yuk mashinasi.",
         29900, "https://images.unsplash.com/photo-1601584115197-04ecc0da31d7?w=800",
         "", "demo_file_flatbed"),
    ]
    async with db.transaction() as con:
        await con.executemany(
            "INSERT INTO mods (name, category, description, price, image_url, video_url, file_id) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7)", demo,
        )


# ═══════════════════════════════════════════════════════════════
#  USERS
# ═══════════════════════════════════════════════════════════════
async def upsert_user(user_id: int, name: str, username: Optional[str] = None) -> None:
    await db.execute(
        "INSERT INTO users (id, name, username) VALUES ($1,$2,$3) "
        "ON CONFLICT(id) DO UPDATE SET name=excluded.name, username=excluded.username",
        user_id, name, username,
    )


# ═══════════════════════════════════════════════════════════════
#  SUBSCRIPTION
# ═══════════════════════════════════════════════════════════════
TIERS = {
    "free": {"name": "Free", "emoji": "🆓", "mods_limit": 3,   "ai_access": False, "price_uzs": 0},
    "pro":  {"name": "Pro",  "emoji": "⭐", "mods_limit": 20,  "ai_access": True,  "price_uzs": 29_900},
    "max":  {"name": "Max",  "emoji": "💎", "mods_limit": 999, "ai_access": True,  "price_uzs": 59_900},
}


async def get_subscription(user_id: int) -> Dict[str, Any]:
    row = await db.fetchrow(
        "SELECT subscription_tier, subscription_expire FROM users WHERE id=$1", user_id,
    )
    if not row:
        return {"tier": "free", "expire": None, "is_active": True, "info": TIERS["free"]}

    tier = row["subscription_tier"] or "free"
    expire = row["subscription_expire"]
    if expire and date.fromisoformat(expire[:10]) < date.today():
        is_active, tier = False, "free"
    else:
        is_active = True
    return {"tier": tier, "expire": expire, "is_active": is_active,
            "info": TIERS.get(tier, TIERS["free"])}


async def upgrade_subscription(user_id: int, tier: str, days: int = 30) -> str:
    if tier not in ("pro", "max"):
        raise ValueError(f"Noto'g'ri tier: {tier}")
    expire_date = (date.today() + timedelta(days=days)).isoformat()
    await db.execute(
        "INSERT INTO users (id, name, subscription_tier, subscription_expire) "
        "VALUES ($1,$2,$3,$4) "
        "ON CONFLICT(id) DO UPDATE SET subscription_tier=excluded.subscription_tier, "
        "subscription_expire=excluded.subscription_expire",
        user_id, "User", tier, expire_date,
    )
    return expire_date


async def downgrade_expired_subscriptions() -> List[int]:
    today = date.today().isoformat()
    rows = await db.fetch(
        "UPDATE users SET subscription_tier='free', subscription_expire=NULL "
        "WHERE subscription_tier <> 'free' AND subscription_expire IS NOT NULL "
        "AND substr(subscription_expire,1,10) < $1 RETURNING id",
        today,
    )
    return [r["id"] for r in rows]


# ═══════════════════════════════════════════════════════════════
#  MODS
# ═══════════════════════════════════════════════════════════════
async def add_mod(name: str, category: str, description: str, price: int,
                  image_url: str, video_url: str, file_id: str) -> int:
    return await db.fetchval(
        "INSERT INTO mods (name, category, description, price, image_url, video_url, file_id) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id",
        name, category, description, price, image_url, video_url, file_id,
    )


async def get_all_mods(category: Optional[str] = None) -> List[Dict[str, Any]]:
    if category:
        rows = await db.fetch(
            "SELECT * FROM mods WHERE category=$1 ORDER BY created_at DESC", category)
    else:
        rows = await db.fetch("SELECT * FROM mods ORDER BY created_at DESC")
    return [dict(r) for r in rows]


async def get_mod_by_id(mod_id: int) -> Optional[Dict[str, Any]]:
    row = await db.fetchrow("SELECT * FROM mods WHERE id=$1", mod_id)
    return dict(row) if row else None


async def delete_mod(mod_id: int) -> bool:
    return await db.execute_count("DELETE FROM mods WHERE id=$1", mod_id) > 0


# ═══════════════════════════════════════════════════════════════
#  ORDERS
# ═══════════════════════════════════════════════════════════════
async def create_order(user_id: int, mod_id: int, amount: int, method: str) -> int:
    return await db.fetchval(
        "INSERT INTO orders (user_id, mod_id, amount, payment_method) "
        "VALUES ($1,$2,$3,$4) RETURNING id",
        user_id, mod_id, amount, method,
    )


async def mark_order_paid(order_id: int, transaction_id: str) -> None:
    await db.execute(
        f"UPDATE orders SET status='paid', transaction_id=$1, paid_at={_NOW_SQL} WHERE id=$2",
        transaction_id, order_id,
    )


async def has_purchased(user_id: int, mod_id: int) -> bool:
    return await db.fetchval(
        "SELECT EXISTS(SELECT 1 FROM orders WHERE user_id=$1 AND mod_id=$2 AND status='paid')",
        user_id, mod_id,
    )


async def get_user_orders(user_id: int) -> List[Dict[str, Any]]:
    rows = await db.fetch(
        "SELECT o.id, m.name, m.category, o.amount, o.paid_at "
        "FROM orders o JOIN mods m ON o.mod_id=m.id "
        "WHERE o.user_id=$1 AND o.status='paid' ORDER BY o.paid_at DESC",
        user_id,
    )
    return [dict(r) for r in rows]


# Back-compat aliases: handlers call these names (previously undefined → crashes).
has_user_purchased = has_purchased
get_user_purchases = get_user_orders


async def set_mod_key(mod_id: int, mod_key: str) -> bool:
    return await db.execute_count("UPDATE mods SET mod_key=$1 WHERE id=$2", mod_key, mod_id) == 1


async def set_mod_image(mod_id: int, image_url: str) -> None:
    await db.execute("UPDATE mods SET image_url=$1 WHERE id=$2", image_url, mod_id)


async def increment_downloads(mod_id: int) -> None:
    await db.execute("UPDATE mods SET downloads = downloads + 1 WHERE id=$1", mod_id)
