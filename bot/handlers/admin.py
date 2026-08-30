"""
handlers/admin.py — Admin mod yuklash oqimi.

Kanal oqimi (yangi):
  1. Admin kanalga fayl yuboradi
  2. Bot file_id ni ushlaydi, _pending_file ga saqlaydi (yuklab olmaydi)
  3. Bot admin DM-iga: "Fayl tayyor! /addmod bosing" deb yozadi
  4. Admin /addmod bosadi → FSM boshlanadi (nom, kategoriya, tavsif, narx, video, rasm)
  5. Tayyor → bazaga yozadi

Guruh oqimi (avvalgi):
  Guruhda bot admin bo'lsa ham xuddi shu /addmod orqali ishlaydi.
"""
from aiogram import Router, F, Bot, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    ReplyKeyboardRemove,
)

from bot.config import ADMIN_ID, ARCHIVE_GROUP_ID
from bot import database as db

router = Router()


# ─────────────────────────────────────────────
#  Kutayotgan faylni vaqtincha saqlash
#  (FSM kanalda ishlamaydi, shuning uchun oddiy dict)
# ─────────────────────────────────────────────

_pending_file: dict = {}   # { admin_id: {file_id, file_name, size_mb} }


# ─────────────────────────────────────────────
#  FSM Holatlari
# ─────────────────────────────────────────────

class AddMod(StatesGroup):
    waiting_name        = State()
    waiting_category    = State()
    waiting_description = State()
    waiting_price       = State()
    waiting_video       = State()
    waiting_image       = State()


# ─────────────────────────────────────────────
#  UI elementlari
# ─────────────────────────────────────────────

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


# ─────────────────────────────────────────────
#  1a. KANALDA fayl ushlanadi (channel_post)
#     from_user yo'q, shuning uchun FSM yo'q
#     Fayl ID ni _pending_file ga saqlab, DM ga xabar
# ─────────────────────────────────────────────

@router.channel_post(
    F.chat.id == ARCHIVE_GROUP_ID,
    F.document | F.video | F.audio,
)
async def capture_channel_file(message: types.Message, bot: Bot):
    """Kanalga yuklangan faylni ushlaydi (yuklamaydi)."""
    file_obj  = message.document or message.video or message.audio
    if not file_obj:
        return

    file_id   = file_obj.file_id
    file_name = getattr(file_obj, "file_name", "fayl")
    size_mb   = round(getattr(file_obj, "file_size", 0) / (1024 * 1024), 1)

    # Vaqtincha saqlaymiz
    _pending_file[ADMIN_ID] = {
        "file_id":   file_id,
        "file_name": file_name,
        "size_mb":   size_mb,
    }

    # Admin DM ga xabar
    await bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            f"<b>Yangi fayl aniqlandi!</b>\n\n"
            f"<code>{file_name}</code>   {size_mb} MB\n\n"
            f"Ma'lumotlarni kiritish uchun:\n"
            f"/addmod — bosing"
        ),
        parse_mode="HTML",
    )


# ─────────────────────────────────────────────
#  1b. GURUHDA fayl ushlanadi (message)
#     from_user bor, FSM darhol boshlanadi
# ─────────────────────────────────────────────

@router.message(
    F.chat.id == ARCHIVE_GROUP_ID,
    F.document | F.video | F.audio,
)
async def capture_group_file(message: types.Message, bot: Bot, state: FSMContext):
    """Guruhga yuklangan faylni ushlaydi va FSM boshlanadi."""
    file_obj  = message.document or message.video or message.audio
    if not file_obj:
        return

    file_id   = file_obj.file_id
    file_name = getattr(file_obj, "file_name", "fayl")
    size_mb   = round(getattr(file_obj, "file_size", 0) / (1024 * 1024), 1)

    _pending_file[ADMIN_ID] = {
        "file_id":   file_id,
        "file_name": file_name,
        "size_mb":   size_mb,
    }

    await bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            f"<b>Yangi fayl aniqlandi!</b>\n\n"
            f"<code>{file_name}</code>   {size_mb} MB\n\n"
            f"/addmod — ma'lumotlarni kiritish"
        ),
        parse_mode="HTML",
    )


# ─────────────────────────────────────────────
#  2. /addmod — FSM ni boshlash (Admin DM)
# ─────────────────────────────────────────────

@router.message(
    Command("addmod"),
    F.from_user.id == ADMIN_ID,
    F.chat.type == "private",
)
async def cmd_addmod(message: types.Message, state: FSMContext):
    """Admin /addmod berganda FSM ni boshlaydi."""
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
        f"1/6 — Mod <b>nomini</b> kiriting:",
        parse_mode="HTML",
        reply_markup=_cancel_kb(),
    )


# ─────────────────────────────────────────────
#  Bekor qilish
# ─────────────────────────────────────────────

@router.message(
    F.text == "❌ Bekor qilish",
    F.from_user.id == ADMIN_ID,
    F.chat.type == "private",
)
async def cancel_upload(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Mod qo'shish bekor qilindi.", reply_markup=ReplyKeyboardRemove())


# ─────────────────────────────────────────────
#  3-8. FSM bosqichlari
# ─────────────────────────────────────────────

@router.message(AddMod.waiting_name, F.chat.type == "private", F.from_user.id == ADMIN_ID)
async def step_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await message.answer("2/6 — Kategoriyani tanlang:", reply_markup=CATEGORY_KB)
    await state.set_state(AddMod.waiting_category)


@router.message(AddMod.waiting_category, F.chat.type == "private", F.from_user.id == ADMIN_ID)
async def step_category(message: types.Message, state: FSMContext):
    cat = CATEGORY_MAP.get(message.text)
    if not cat:
        await message.answer("Iltimos, tugmalardan birini tanlang.")
        return
    await state.update_data(category=cat)
    await message.answer(
        "3/6 — Mod <b>tavsifini</b> kiriting (1-3 jumla):",
        parse_mode="HTML",
        reply_markup=_cancel_kb(),
    )
    await state.set_state(AddMod.waiting_description)


@router.message(AddMod.waiting_description, F.chat.type == "private", F.from_user.id == ADMIN_ID)
async def step_description(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text.strip())
    await message.answer(
        "4/6 — <b>Narxini</b> kiriting (UZS, faqat raqam):\n<i>Masalan: 49900</i>",
        parse_mode="HTML",
    )
    await state.set_state(AddMod.waiting_price)


@router.message(AddMod.waiting_price, F.chat.type == "private", F.from_user.id == ADMIN_ID)
async def step_price(message: types.Message, state: FSMContext):
    try:
        price = int(message.text.strip().replace(" ", "").replace(",", ""))
    except ValueError:
        await message.answer("Faqat raqam kiriting. Masalan: <code>49900</code>", parse_mode="HTML")
        return
    await state.update_data(price=price)
    await message.answer(
        "5/6 — <b>YouTube Shorts URL</b> kiriting:\n"
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
        "6/6 — <b>Thumbnail rasm URL</b> kiriting:\n"
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
        price       = data["price"],
        image_url   = image_url,
        video_url   = data.get("video_url", ""),
        file_id     = data["file_id"],
    )

    price_fmt = f"{data['price']:,}".replace(",", " ")
    await message.answer(
        f"<b>Mod bazaga qo'shildi!</b>\n\n"
        f"ID: <code>{mod_id}</code>\n"
        f"Nom: <b>{data['name']}</b>\n"
        f"Kategoriya: {data['category']}\n"
        f"Narx: {price_fmt} UZS\n\n"
        f"Katalogda ko'rinadi",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )


# ─────────────────────────────────────────────
#  Admin buyruqlari
# ─────────────────────────────────────────────

@router.message(Command("admin"), F.from_user.id == ADMIN_ID)
async def cmd_admin(message: types.Message):
    mods = await db.get_all_mods()
    pending = "Ha" if ADMIN_ID in _pending_file else "Yo'q"
    pf = _pending_file.get(ADMIN_ID, {})
    pending_name = pf.get("file_name", "-")
    await message.answer(
        f"<b>Admin Panel</b>\n\n"
        f"Bazadagi modlar: <b>{len(mods)} ta</b>\n"
        f"Kutayotgan fayl: <b>{pending}</b>"
        + (f" — <code>{pending_name}</code>" if pending == "Ha" else "") +
        f"\n\n/addmod — Yangi mod kiritish\n"
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
        pf = f"{m['price']:,}".replace(",", " ")
        lines.append(f"#{m['id']} {m['name']} | {m['category']} | {pf} UZS")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("deletemod"), F.from_user.id == ADMIN_ID)
async def cmd_deletemod(message: types.Message):
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Ishlatish: /deletemod &lt;mod_id&gt;", parse_mode="HTML")
        return
    deleted = await db.delete_mod(int(parts[1]))
    if deleted:
        await message.answer(f"Mod #{parts[1]} o'chirildi.")
    else:
        await message.answer(f"Mod #{parts[1]} topilmadi.")
