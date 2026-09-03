"""
handlers/admin.py — Admin mod yuklash oqimi.

YANGI AVTOMATIK OQIM:
  1. Admin kanalga RASM + ZIP fayl yuboradi
  2. Bot rasmni va zip faylni avtomatik ushlaydi
  3. Ikkisi ham kelganda — mod avtomatik yaratiladi:
     - Nom = zip fayl nomidan (title case, .zip olib tashlanadi)
     - Kategoriya = fayl nomidan auto-detect (yoki default "cars")
     - Tavsif = auto
     - Narx = 0 (obuna tizimi ishlaydi)
     - Rasm = Telegram photo file_id
     - Fayl = zip file_id
  4. mods.json yangilanib, GitHub/Vercel ga push qilinadi

QOLDA OQIM (saqlanadi):
  /addmod — eski 6 bosqichli FSM (kerak bo'lganda ishlatiladi)
"""
import asyncio
import logging
import re
from aiogram import Router, F, Bot, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    ReplyKeyboardRemove,
)

from bot.config import ADMIN_ID, ARCHIVE_GROUP_ID, BOT_TOKEN
from bot import database as db

router = Router()
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
#  AVTOMATIK MOD QO'SHISH TIZIMI
# ═══════════════════════════════════════════════════════════════

# Kanaldan kelgan rasm va faylni vaqtincha saqlash
_pending_auto: dict = {}
# Format: { "photo_id": str, "file_id": str, "file_name": str, "size_mb": float }

# Qo'lda mod qo'shish uchun (eski /addmod FSM)
_pending_file: dict = {}   # { admin_id: {file_id, file_name, size_mb} }

# Auto-create timer (rasm va fayl 30 sek ichida kelishi kerak)
_auto_timer_task = None


def _guess_category(file_name: str) -> str:
    """Fayl nomidan kategoriyani aniqlash."""
    name_lower = file_name.lower()
    if any(w in name_lower for w in ("truck", "kamaz", "maz", "daf", "scania", "volvo_truck", "lorry")):
        return "trucks"
    if any(w in name_lower for w in ("map", "xarita", "city", "track", "road", "terrain")):
        return "maps"
    if any(w in name_lower for w in ("3d", "model", "blender", "mesh", "obj", "fbx")):
        return "3d_models"
    return "cars"


def _clean_mod_name(file_name: str) -> str:
    """Zip fayl nomidan chiroyli mod nomi yasash.
    
    Misollar:
      Mercedes_AMG_G63.zip       → Mercedes AMG G63
      bmw_m5_f90_competition.zip → Bmw M5 F90 Competition
      Nissan-GT-R-R35.zip        → Nissan GT R R35
    """
    # Kengaytmani olib tashlash
    name = re.sub(r'\.(zip|rar|7z|tar\.gz)$', '', file_name, flags=re.IGNORECASE)
    # _ va - ni probel bilan almashtirish
    name = name.replace("_", " ").replace("-", " ")
    # Ortiqcha probellarni tozalash
    name = re.sub(r'\s+', ' ', name).strip()
    # Title case (har bir so'z bosh harf bilan)
    name = name.title()
    return name


async def _try_auto_create(bot: Bot):
    """
    Agar ham rasm, ham fayl mavjud bo'lsa — avtomatik mod yaratadi.
    Ikkisi birga kelgunga qadar kutadi.
    """
    photo_id  = _pending_auto.get("photo_id")
    file_id   = _pending_auto.get("file_id")
    file_name = _pending_auto.get("file_name", "mod.zip")

    if not photo_id or not file_id:
        return  # Hali ikkisi kelmagan — kutamiz

    # ── Ikkisi ham bor — mod yaratamiz! ──────────────────────
    mod_name    = _clean_mod_name(file_name)
    category    = _guess_category(file_name)
    description = f"BeamNG.drive mod: {mod_name}"
    size_mb     = _pending_auto.get("size_mb", 0)

    # Rasm URL sifatida Telegram photo file_id saqlaymiz
    # Mini App /api/photo/ endpoint orqali ko'rsatadi
    image_url = f"tg_photo:{photo_id}"

    mod_id = await db.add_mod(
        name        = mod_name,
        category    = category,
        description = description,
        price       = 0,
        image_url   = image_url,
        video_url   = "",
        file_id     = file_id,
    )

    # Pending ni tozalash
    _pending_auto.clear()

    # Admin DM ga tasdiqlash
    await bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            f"<b>Mod avtomatik qo'shildi!</b>\n\n"
            f"ID: <code>{mod_id}</code>\n"
            f"Nom: <b>{mod_name}</b>\n"
            f"Kategoriya: {category}\n"
            f"Fayl: <code>{file_name}</code>  ({size_mb} MB)\n\n"
            f"Katalog yangilanmoqda..."
        ),
        parse_mode="HTML",
    )

    logger.info(f"[Auto] Mod #{mod_id} '{mod_name}' avtomatik qo'shildi")

    # GitHub/Vercel ga push
    await sync_mods_to_github()


# ─────────────────────────────────────────────
#  KANAL HANDLERLARI — AVTOMATIK REJIM
# ─────────────────────────────────────────────

@router.channel_post(
    F.chat.id == ARCHIVE_GROUP_ID,
    F.photo,
)
async def capture_channel_photo(message: types.Message, bot: Bot):
    """Kanalga yuklangan rasmni ushlaydi."""
    # Eng katta o'lchamdagi rasmni olish
    photo = message.photo[-1]  # Oxirgi = eng katta
    photo_id = photo.file_id

    _pending_auto["photo_id"] = photo_id
    logger.info(f"[Auto] Rasm aniqlandi: {photo_id[:30]}...")

    # Rasm va fayl birga kelganmi tekshirish
    # Kichik kutish — agar fayl ham shu-shu kelsa
    await asyncio.sleep(2)
    await _try_auto_create(bot)


@router.channel_post(
    F.chat.id == ARCHIVE_GROUP_ID,
    F.document | F.video | F.audio,
)
async def capture_channel_file(message: types.Message, bot: Bot):
    """Kanalga yuklangan faylni ushlaydi (yuklamaydi)."""
    file_obj = message.document or message.video or message.audio
    if not file_obj:
        return

    file_id   = file_obj.file_id
    file_name = getattr(file_obj, "file_name", "mod.zip")
    size_mb   = round(getattr(file_obj, "file_size", 0) / (1024 * 1024), 1)

    # Avtomatik rejim uchun saqlash
    _pending_auto["file_id"]   = file_id
    _pending_auto["file_name"] = file_name
    _pending_auto["size_mb"]   = size_mb

    # Eski qo'lda rejim uchun ham saqlash
    _pending_file[ADMIN_ID] = {
        "file_id":   file_id,
        "file_name": file_name,
        "size_mb":   size_mb,
    }

    logger.info(f"[Auto] Fayl aniqlandi: {file_name} ({size_mb} MB)")

    # Rasm va fayl birga kelganmi tekshirish
    await asyncio.sleep(2)
    await _try_auto_create(bot)

    # Agar avtomatik yaratilmagan bo'lsa (rasm yo'q), admin ga xabar
    if _pending_auto.get("file_id") and not _pending_auto.get("photo_id"):
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"<b>Fayl aniqlandi!</b>\n\n"
                f"<code>{file_name}</code>   {size_mb} MB\n\n"
                f"Avtomatik qo'shish uchun kanalga <b>rasm</b> ham yuboring.\n"
                f"Yoki qo'lda: /addmod"
            ),
            parse_mode="HTML",
        )


# ─────────────────────────────────────────────
#  GURUH HANDLERI (eski — saqlanadi)
# ─────────────────────────────────────────────

@router.message(
    F.chat.id == ARCHIVE_GROUP_ID,
    F.photo,
)
async def capture_group_photo(message: types.Message, bot: Bot):
    """Guruhga yuklangan rasmni ushlaydi."""
    photo = message.photo[-1]
    _pending_auto["photo_id"] = photo.file_id
    await asyncio.sleep(2)
    await _try_auto_create(bot)


@router.message(
    F.chat.id == ARCHIVE_GROUP_ID,
    F.document | F.video | F.audio,
)
async def capture_group_file(message: types.Message, bot: Bot, state: FSMContext):
    """Guruhga yuklangan faylni ushlaydi."""
    file_obj = message.document or message.video or message.audio
    if not file_obj:
        return

    file_id   = file_obj.file_id
    file_name = getattr(file_obj, "file_name", "mod.zip")
    size_mb   = round(getattr(file_obj, "file_size", 0) / (1024 * 1024), 1)

    _pending_auto["file_id"]   = file_id
    _pending_auto["file_name"] = file_name
    _pending_auto["size_mb"]   = size_mb

    _pending_file[ADMIN_ID] = {
        "file_id": file_id, "file_name": file_name, "size_mb": size_mb,
    }

    await asyncio.sleep(2)
    await _try_auto_create(bot)

    if _pending_auto.get("file_id") and not _pending_auto.get("photo_id"):
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"<b>Fayl aniqlandi!</b>\n"
                f"<code>{file_name}</code> {size_mb} MB\n\n"
                f"Kanalga <b>rasm</b> yuboring yoki /addmod bosing."
            ),
            parse_mode="HTML",
        )


# ═══════════════════════════════════════════════════════════════
#  QO'LDA MOD QO'SHISH — FSM (/addmod)
# ═══════════════════════════════════════════════════════════════

class AddMod(StatesGroup):
    waiting_name        = State()
    waiting_category    = State()
    waiting_description = State()
    waiting_video       = State()
    waiting_image       = State()


CATEGORY_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🚗 Cars"),    KeyboardButton(text="🚛 Trucks")],
        [KeyboardButton(text="🗺️ Maps"),   KeyboardButton(text="🎨 3D Models")],
        [KeyboardButton(text="❌ Bekor qilish")],
    ],
    resize_keyboard=True,
    one_time_keyboard=True,
)

CATEGORY_MAP = {
    "🚗 Cars":       "cars",
    "🚛 Trucks":     "trucks",
    "🗺️ Maps":      "maps",
    "🎨 3D Models": "3d_models",
}

def _cancel_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Bekor qilish")]],
        resize_keyboard=True,
    )


@router.message(
    Command("addmod"),
    F.from_user.id == ADMIN_ID,
    F.chat.type == "private",
)
async def cmd_addmod(message: types.Message, state: FSMContext):
    """Admin /addmod berganda FSM ni boshlaydi (narxsiz — 5 bosqich)."""
    if ADMIN_ID not in _pending_file:
        await message.answer(
            "Hali fayl yuklanmagan.\n"
            "Avval kanalga mod faylini yuboring, keyin /addmod bosing.",
        )
        return

    pf = _pending_file[ADMIN_ID]
    await state.update_data(
        file_id   = pf["file_id"],
        file_name = pf["file_name"],
    )
    await state.set_state(AddMod.waiting_name)

    await message.answer(
        f"<b>Mod qo'shish boshlandi!</b>\n\n"
        f"Fayl: <code>{pf['file_name']}</code>  ({pf['size_mb']} MB)\n\n"
        f"1/5 — Mod <b>nomini</b> kiriting:",
        parse_mode="HTML",
        reply_markup=_cancel_kb(),
    )


@router.message(
    F.text == "❌ Bekor qilish",
    F.from_user.id == ADMIN_ID,
    F.chat.type == "private",
)
async def cancel_upload(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Mod qo'shish bekor qilindi.", reply_markup=ReplyKeyboardRemove())


@router.message(AddMod.waiting_name, F.chat.type == "private", F.from_user.id == ADMIN_ID)
async def step_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await message.answer("2/5 — Kategoriyani tanlang:", reply_markup=CATEGORY_KB)
    await state.set_state(AddMod.waiting_category)


@router.message(AddMod.waiting_category, F.chat.type == "private", F.from_user.id == ADMIN_ID)
async def step_category(message: types.Message, state: FSMContext):
    cat = CATEGORY_MAP.get(message.text)
    if not cat:
        await message.answer("Iltimos, tugmalardan birini tanlang.")
        return
    await state.update_data(category=cat)
    await message.answer(
        "3/5 — Mod <b>tavsifini</b> kiriting (1-3 jumla):",
        parse_mode="HTML",
        reply_markup=_cancel_kb(),
    )
    await state.set_state(AddMod.waiting_description)


@router.message(AddMod.waiting_description, F.chat.type == "private", F.from_user.id == ADMIN_ID)
async def step_description(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text.strip())
    await message.answer(
        "4/5 — <b>YouTube Shorts URL</b> kiriting:\n"
        "<i>Masalan: https://youtube.com/shorts/xxxxx</i>\n\n"
        "Yo'q bo'lsa <code>-</code> yuboring.",
        parse_mode="HTML",
    )
    await state.set_state(AddMod.waiting_video)


@router.message(AddMod.waiting_video, F.chat.type == "private", F.from_user.id == ADMIN_ID)
async def step_video(message: types.Message, state: FSMContext):
    video_url = "" if message.text.strip() == "-" else message.text.strip()
    await state.update_data(video_url=video_url)
    await message.answer(
        "5/5 — <b>Thumbnail rasm URL</b> kiriting:\n"
        "Yo'q bo'lsa <code>-</code> yuboring.",
        parse_mode="HTML",
    )
    await state.set_state(AddMod.waiting_image)


@router.message(AddMod.waiting_image, F.chat.type == "private", F.from_user.id == ADMIN_ID)
async def step_image(message: types.Message, state: FSMContext):
    image_url = "" if message.text.strip() == "-" else message.text.strip()
    data      = await state.get_data()
    await state.clear()

    # Pending faylni tozalash
    _pending_file.pop(ADMIN_ID, None)

    mod_id = await db.add_mod(
        name        = data["name"],
        category    = data["category"],
        description = data["description"],
        price       = 0,
        image_url   = image_url,
        video_url   = data.get("video_url", ""),
        file_id     = data["file_id"],
    )

    await message.answer(
        f"<b>Mod bazaga qo'shildi!</b>\n\n"
        f"ID: <code>{mod_id}</code>\n"
        f"Nom: <b>{data['name']}</b>\n"
        f"Kategoriya: {data['category']}\n\n"
        f"Katalog yangilanmoqda...",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )

    await sync_mods_to_github()


# ═══════════════════════════════════════════════════════════════
#  SYNC va ADMIN BUYRUQLARI
# ═══════════════════════════════════════════════════════════════

async def sync_mods_to_github():
    """Barcha modlarni mods.json ga eksport qilib, GitHub/Vercel ga push qiladi."""
    try:
        import json, subprocess

        mods = await db.get_all_mods()

        # Rasm URL larni to'g'rilash — tg_photo: prefixli rasmlar uchun
        # Telegram Bot API getFile URL yasaymiz
        for mod in mods:
            img = mod.get("image_url", "")
            if img.startswith("tg_photo:"):
                photo_file_id = img.replace("tg_photo:", "")
                mod["image_url"] = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={photo_file_id}"
                # Thumbnail sifatida to'g'ridan Telegram CDN ishlatamiz
                mod["thumbnail"] = f"/api/photo/{photo_file_id}"

        with open("mods.json", "w", encoding="utf-8") as f:
            json.dump({"ok": True, "count": len(mods), "mods": mods}, f, indent=2, ensure_ascii=False)
        subprocess.run(["git", "add", "mods.json"], capture_output=True)
        subprocess.run(["git", "commit", "-m", "auto: sync mods.json to Vercel"], capture_output=True)
        subprocess.run(["git", "push", "origin", "main"], capture_output=True)
        logger.info("[SYNC] mods.json push qilindi")
    except Exception as e:
        logger.error(f"[SYNC] Xatolik: {e}")


@router.message(Command("admin"), F.from_user.id == ADMIN_ID)
async def cmd_admin(message: types.Message):
    mods = await db.get_all_mods()
    pending = "Ha" if ADMIN_ID in _pending_file else "Yo'q"
    pf = _pending_file.get(ADMIN_ID, {})
    pending_name = pf.get("file_name", "-")
    auto_photo = "Ha" if _pending_auto.get("photo_id") else "Yo'q"
    auto_file  = "Ha" if _pending_auto.get("file_id") else "Yo'q"
    await message.answer(
        f"<b>Admin Panel</b>\n\n"
        f"Bazadagi modlar: <b>{len(mods)} ta</b>\n"
        f"Kutayotgan fayl: <b>{pending}</b>"
        + (f" — <code>{pending_name}</code>" if pending == "Ha" else "") +
        f"\n\n<b>Avtomatik rejim:</b>\n"
        f"Rasm: {auto_photo}   |   Fayl: {auto_file}\n\n"
        f"/addmod — Qo'lda mod kiritish\n"
        f"/listmods — Ro'yxat\n"
        f"/deletemod &lt;id&gt; — O'chirish",
        parse_mode="HTML",
    )


@router.message(Command("listmods"), F.from_user.id == ADMIN_ID)
async def cmd_listmods(message: types.Message):
    mods = await db.get_all_mods()
    if not mods:
        await message.answer("Hali mod yo'q.")
        return
    lines = [f"<b>Modlar ({len(mods)} ta):</b>\n"]
    for m in mods:
        lines.append(f"#{m['id']} {m['name']} | {m['category']}")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("deletemod"), F.from_user.id == ADMIN_ID)
async def cmd_deletemod(message: types.Message):
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Ishlatish: /deletemod &lt;mod_id&gt;", parse_mode="HTML")
        return
    deleted = await db.delete_mod(int(parts[1]))
    if deleted:
        await sync_mods_to_github()
        await message.answer(f"Mod #{parts[1]} o'chirildi va katalog yangilandi.")
    else:
        await message.answer(f"Mod #{parts[1]} topilmadi.")
