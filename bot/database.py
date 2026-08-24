"""
database.py — SQLite baza init + barcha CRUD operatsiyalari.
aiosqlite yordamida to'liq async.
"""
import aiosqlite
import asyncio
from typing import Optional, List, Dict, Any

DB_PATH = "beamods.db"


async def init_db() -> None:
    """Barcha jadvallarni yaratadi va namunaviy ma'lumotlar qo'shadi."""
    async with aiosqlite.connect(DB_PATH) as db:
        # ── Users jadvali ──────────────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY,        -- Telegram user_id
                full_name   TEXT    NOT NULL,
                username    TEXT,
                joined_at   TEXT    DEFAULT (datetime('now')),
                is_banned   INTEGER DEFAULT 0
            )
        """)

        # ── Mods jadvali ───────────────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS mods (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT    NOT NULL,
                category     TEXT    NOT NULL,           -- cars | trucks | maps | 3d_models
                description  TEXT,
                price        INTEGER NOT NULL DEFAULT 0, -- UZS so'mda
                file_id      TEXT    NOT NULL,           -- Telegram file_id (katta fayl)
                youtube_url  TEXT,                       -- YouTube Shorts URL
                thumbnail    TEXT,                       -- Rasm URL yoki path
                downloads    INTEGER DEFAULT 0,
                created_at   TEXT    DEFAULT (datetime('now'))
            )
        """)

        # ── Orders jadvali ─────────────────────────────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        INTEGER NOT NULL,
                mod_id         INTEGER NOT NULL,
                amount         INTEGER NOT NULL,         -- To'lov miqdori (UZS)
                payment_method TEXT,                     -- click | payme | free
                payment_status TEXT    DEFAULT 'pending', -- pending | paid | failed
                transaction_id TEXT,                     -- To'lov tizimidan kelgan ID
                created_at     TEXT    DEFAULT (datetime('now')),
                paid_at        TEXT,
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (mod_id)  REFERENCES mods  (id)
            )
        """)

        # ── Namunaviy modlar (faqat baza bo'sh bo'lsa) ─────────────────
        cursor = await db.execute("SELECT COUNT(*) FROM mods")
        count = (await cursor.fetchone())[0]

        if count == 0:
            demo_mods = [
                (
                    "BMW M5 F90 Competition",
                    "cars",
                    "Haqiqiy PBR teksturalar, to'g'ri Jbeam fizikasi, V8 motor ovozlari bilan.",
                    49900,
                    "demo_file_id_bmw",
                    "https://www.youtube.com/shorts/example1",
                    "https://images.unsplash.com/photo-1555215695-3004980ad54e?w=800",
                ),
                (
                    "Kamaz 6520 Offroad",
                    "trucks",
                    "Og'ir offroad yuk mashinasi — shina yorilishi va sakrash fizikasi.",
                    34900,
                    "demo_file_id_kamaz",
                    "https://www.youtube.com/shorts/example2",
                    "https://images.unsplash.com/photo-1601584115197-04ecc0da31d7?w=800",
                ),
                (
                    "Toshkent Shahri Xaritasi",
                    "maps",
                    "Toshkent ko'chalari, tungi chiroqlar, 20+ km² ochiq dunyo.",
                    24900,
                    "demo_file_id_tashkent",
                    "https://www.youtube.com/shorts/example3",
                    "https://images.unsplash.com/photo-1578895101408-1a36b834405b?w=800",
                ),
                (
                    "Nissan GT-R R35 Nismo",
                    "cars",
                    "600 HP, drift spec, Launch Control, sport egzoz effektlari.",
                    59900,
                    "demo_file_id_gtr",
                    "https://www.youtube.com/shorts/example4",
                    "https://images.unsplash.com/photo-1617788138017-80ad40651399?w=800",
                ),
                (
                    "BeamNG Flatbed Truck Pro",
                    "trucks",
                    "Keng yuk o'rinli, offroad uchun mo'ljallangan flatbed yuk mashinasi.",
                    29900,
                    "demo_file_id_flatbed",
                    "https://www.youtube.com/shorts/example5",
                    "https://images.unsplash.com/photo-1601584115197-04ecc0da31d7?w=800",
                ),
            ]
            await db.executemany(
                """
                INSERT INTO mods (name, category, description, price, file_id, youtube_url, thumbnail)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                demo_mods,
            )

        await db.commit()
    print("✅ Ma'lumotlar bazasi muvaffaqiyatli ishga tushdi.")


# ── Users ──────────────────────────────────────────────────────────────────────

async def upsert_user(user_id: int, full_name: str, username: Optional[str]) -> None:
    """Foydalanuvchini qo'shadi yoki mavjud bo'lsa yangilaydi."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO users (id, full_name, username)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET full_name=excluded.full_name, username=excluded.username
            """,
            (user_id, full_name, username),
        )
        await db.commit()


async def get_user_purchases(user_id: int) -> List[Dict[str, Any]]:
    """Foydalanuvchining barcha to'langan buyurtmalarini qaytaradi."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT o.id, m.name, m.category, o.amount, o.paid_at
            FROM orders o
            JOIN mods m ON o.mod_id = m.id
            WHERE o.user_id = ? AND o.payment_status = 'paid'
            ORDER BY o.paid_at DESC
            """,
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


# ── Mods ───────────────────────────────────────────────────────────────────────

async def get_all_mods(category: Optional[str] = None) -> List[Dict[str, Any]]:
    """Barcha yoki kategoriya bo'yicha filtrlangan modlarni qaytaradi."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if category:
            cursor = await db.execute(
                "SELECT * FROM mods WHERE category = ? ORDER BY created_at DESC",
                (category,),
            )
        else:
            cursor = await db.execute("SELECT * FROM mods ORDER BY created_at DESC")
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_mod_by_id(mod_id: int) -> Optional[Dict[str, Any]]:
    """ID bo'yicha bitta mod ma'lumotini qaytaradi."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM mods WHERE id = ?", (mod_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def add_mod(
    name: str,
    category: str,
    description: str,
    price: int,
    file_id: str,
    youtube_url: str,
    thumbnail: str = "",
) -> int:
    """Yangi mod qo'shadi va uning ID sini qaytaradi."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO mods (name, category, description, price, file_id, youtube_url, thumbnail)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (name, category, description, price, file_id, youtube_url, thumbnail),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore


async def get_mods_with_youtube() -> List[Dict[str, Any]]:
    """YouTube URL mavjud bo'lgan barcha modlarni qaytaradi (Shorts Feed uchun)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM mods WHERE youtube_url IS NOT NULL AND youtube_url != '' ORDER BY created_at DESC"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


# ── Orders ─────────────────────────────────────────────────────────────────────

async def create_order(user_id: int, mod_id: int, amount: int, payment_method: str) -> int:
    """Yangi buyurtma yaratadi va uning ID sini qaytaradi."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO orders (user_id, mod_id, amount, payment_method)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, mod_id, amount, payment_method),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore


async def get_order_by_id(order_id: int) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def mark_order_paid(order_id: int, transaction_id: str) -> None:
    """Buyurtma statusini 'paid' ga o'zgartiradi."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE orders
            SET payment_status = 'paid', transaction_id = ?, paid_at = datetime('now')
            WHERE id = ?
            """,
            (transaction_id, order_id),
        )
        await db.commit()


async def has_user_purchased(user_id: int, mod_id: int) -> bool:
    """Foydalanuvchi bu modni sotib olganmi yoki yo'qmi."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT 1 FROM orders
            WHERE user_id = ? AND mod_id = ? AND payment_status = 'paid'
            LIMIT 1
            """,
            (user_id, mod_id),
        )
        return await cursor.fetchone() is not None


if __name__ == "__main__":
    asyncio.run(init_db())
