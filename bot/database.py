"""
database.py — BeamModsStudio uchun to'liq async SQLite qatlami.

Jadvallar:
  • users  — Telegram foydalanuvchilari
  • mods   — Sotuvdagi modlar (file_id bilan)
  • orders — To'lov buyurtmalari

Barcha funksiyalar asyncio / aiosqlite bilan ishlaydi.
"""
import aiosqlite
import asyncio
from typing import Optional, List, Dict, Any

DB_PATH = "beamods.db"


# ═══════════════════════════════════════════════════════════════
#  INIT
# ═══════════════════════════════════════════════════════════════

async def init_db() -> None:
    """Jadvallarni yaratadi. Birinchi ishga tushirishda demo ma'lumot qo'shadi."""
    async with aiosqlite.connect(DB_PATH) as db:

        # ── users ──────────────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY,   -- Telegram user_id
                name        TEXT    NOT NULL,
                username    TEXT,
                joined_at   TEXT    DEFAULT (datetime('now'))
            )
        """)

        # ── mods ───────────────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS mods (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    NOT NULL,
                category    TEXT    NOT NULL,
                description TEXT,
                price       INTEGER NOT NULL DEFAULT 0,
                image_url   TEXT,
                video_url   TEXT,
                file_id     TEXT    NOT NULL,
                downloads   INTEGER DEFAULT 0,
                created_at  TEXT    DEFAULT (datetime('now'))
            )
        """)

        # ── orders ─────────────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        INTEGER NOT NULL,
                mod_id         INTEGER NOT NULL,
                amount         INTEGER NOT NULL,
                payment_method TEXT    DEFAULT 'click',
                status         TEXT    DEFAULT 'pending',
                transaction_id TEXT,
                created_at     TEXT    DEFAULT (datetime('now')),
                paid_at        TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (mod_id)  REFERENCES mods(id)
            )
        """)

        # Demo ma'lumotlar faqat baza bo'sh bo'lganda qo'shiladi
        cur = await db.execute("SELECT COUNT(*) FROM mods")
        if (await cur.fetchone())[0] == 0:
            await _seed_demo_mods(db)

        await db.commit()
    print("[OK] Database ready.")


async def _seed_demo_mods(db: aiosqlite.Connection) -> None:
    """Ishlab chiqish uchun namunaviy mod ma'lumotlari."""
    demo = [
        ("BMW M5 F90 Competition", "cars",
         "Haqiqiy PBR teksturalar, V8 motor ovozlari bilan.",
         49900,
         "https://images.unsplash.com/photo-1555215695-3004980ad54e?w=800",
         "https://www.youtube.com/shorts/dQw4w9WgXcQ",
         "demo_file_bmw"),

        ("Kamaz 6520 Offroad", "trucks",
         "Og'ir offroad yuk mashinasi — sakrash fizikasi.",
         34900,
         "https://images.unsplash.com/photo-1601584115197-04ecc0da31d7?w=800",
         "https://www.youtube.com/shorts/9bZkp7q19f0",
         "demo_file_kamaz"),

        ("Toshkent Shahri Xaritasi", "maps",
         "20+ km² ochiq dunyo, tungi chiroqlar.",
         24900,
         "https://images.unsplash.com/photo-1578895101408-1a36b834405b?w=800",
         "https://www.youtube.com/shorts/CevxZvSJLk8",
         "demo_file_tashkent"),

        ("Nissan GT-R R35 Nismo", "cars",
         "600 HP drift spec, Launch Control, sport egzoz.",
         59900,
         "https://images.unsplash.com/photo-1617788138017-80ad40651399?w=800",
         "https://www.youtube.com/shorts/kffacxfA7G4",
         "demo_file_gtr"),

        ("BeamNG Flatbed Truck Pro", "trucks",
         "Offroad uchun mo'ljallangan keng yuk mashinasi.",
         29900,
         "https://images.unsplash.com/photo-1601584115197-04ecc0da31d7?w=800",
         "",
         "demo_file_flatbed"),
    ]
    await db.executemany(
        """INSERT INTO mods (name, category, description, price,
                             image_url, video_url, file_id)
           VALUES (?,?,?,?,?,?,?)""",
        demo,
    )


# ═══════════════════════════════════════════════════════════════
#  USERS
# ═══════════════════════════════════════════════════════════════

async def upsert_user(user_id: int, name: str, username: Optional[str] = None) -> None:
    """Foydalanuvchini qo'shadi yoki mavjud bo'lsa yangilaydi (upsert)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO users (id, name, username)
               VALUES (?,?,?)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                                             username=excluded.username""",
            (user_id, name, username),
        )
        await db.commit()


# ═══════════════════════════════════════════════════════════════
#  MODS  —  CRUD
# ═══════════════════════════════════════════════════════════════

async def add_mod(
    name: str,
    category: str,
    description: str,
    price: int,
    image_url: str,
    video_url: str,
    file_id: str,
) -> int:
    """Yangi mod qo'shadi va yangi IDni qaytaradi."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO mods (name, category, description, price,
                                 image_url, video_url, file_id)
               VALUES (?,?,?,?,?,?,?)""",
            (name, category, description, price, image_url, video_url, file_id),
        )
        await db.commit()
        return cur.lastrowid  # type: ignore


async def get_all_mods(category: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Barcha modlarni qaytaradi.
    category='cars' kabi filtr berilsa faqat o'sha kategoriya keladi.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if category:
            cur = await db.execute(
                "SELECT * FROM mods WHERE category=? ORDER BY created_at DESC",
                (category,),
            )
        else:
            cur = await db.execute("SELECT * FROM mods ORDER BY created_at DESC")
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def get_mod_by_id(mod_id: int) -> Optional[Dict[str, Any]]:
    """Bitta modni ID orqali qaytaradi."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM mods WHERE id=?", (mod_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def delete_mod(mod_id: int) -> bool:
    """Modni o'chiradi. True — o'chirildi, False — topilmadi."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM mods WHERE id=?", (mod_id,))
        await db.commit()
        return cur.rowcount > 0


# ═══════════════════════════════════════════════════════════════
#  ORDERS
# ═══════════════════════════════════════════════════════════════

async def create_order(user_id: int, mod_id: int, amount: int, method: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO orders (user_id, mod_id, amount, payment_method)
               VALUES (?,?,?,?)""",
            (user_id, mod_id, amount, method),
        )
        await db.commit()
        return cur.lastrowid  # type: ignore


async def mark_order_paid(order_id: int, transaction_id: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE orders SET status='paid', transaction_id=?,
                                 paid_at=datetime('now')
               WHERE id=?""",
            (transaction_id, order_id),
        )
        await db.commit()


async def has_purchased(user_id: int, mod_id: int) -> bool:
    """Foydalanuvchi modni allaqachon sotib olganmi?"""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM orders WHERE user_id=? AND mod_id=? AND status='paid' LIMIT 1",
            (user_id, mod_id),
        )
        return await cur.fetchone() is not None


async def get_user_orders(user_id: int) -> List[Dict[str, Any]]:
    """Foydalanuvchining to'liq to'langan buyurtmalarini qaytaradi."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT o.id, m.name, m.category, o.amount, o.paid_at
               FROM orders o JOIN mods m ON o.mod_id=m.id
               WHERE o.user_id=? AND o.status='paid'
               ORDER BY o.paid_at DESC""",
            (user_id,),
        )
        return [dict(r) for r in await cur.fetchall()]


if __name__ == "__main__":
    asyncio.run(init_db())
