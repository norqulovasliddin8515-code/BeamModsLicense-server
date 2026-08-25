"""
handlers/admin.py — Admin uchun:

  1. Arxiv guruhida fayl aniqlash → file_id ushlash (yuklab olmaydi).
  2. Admin DM-ida FSM orqali mod ma'lumotlarini yig'ish:
       Nom → Kategoriya → Tavsif → Narx → YouTube URL → Rasm URL
  3. Hamma ma'lumot to'ldirilgandan so'ng bazaga yozish.

FSM holatlari (StatesGroup):
  waiting_name | waiting_category | waiting_description |
  waiting_price | waiting_video | waiting_image
"""
from aiogram import Router, F, Bot, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton,
)

from bot.config import ADMIN_ID, ARCHIVE_GROUP_ID
from bot import database as db

router = Router()


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
#  Yordamchi UI elementlari
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
#  1. Arxiv guruhida fayl ushlanadi
# ─────────────────────────────────────────────

@router.message(
    F.chat.id == ARCHIVE_GROUP_ID,
    F.document | F.video | F.audio,
)
async def capture_file_id(message: types.Message, bot: Bot, state: FSMContext):
    """
    Admin arxiv guruhiga fayl yuborganda chaqiriladi.
    Faylni YUKLAB OLMAYDI — faqat file_id ni oladi.
    Keyin Admin DM-iga o'tib FSM ni boshlaydi.
    """
    file_obj = message.document or message.video or message.audio
    if not file_obj:
        return

    file_id   = file_obj.file_id
    file_name = getattr(file_obj, "file_name", "fayl")
    size_mb   = round(getattr(file_obj, "file_size", 0) / (1024 * 1024), 1)

    # FSM ga file_id ni saqlaymiz
    await state.update_data(file_id=file_id, file_name=file_name)
    await state.set_state(AddMod.waiting_name)

    # Admin DM-iga xabar
    await bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            f"📦 <b>Yangi fayl aniqlandi!</b>\n\n"
            f"📄 <code>{file_name}</code>  •  {size_mb} MB\n"
            f"🔑 file_id: <code>{file_id[:30]}...</code>\n\n"
            f"Ushbu mod uchun <b>nomini</b> yozing 👇"
        ),
        parse_mode="HTML",
        reply_markup=_cancel_kb(),
    )


# ─────────────────────────────────────────────
#  Bekor qilish (istalgan holatda)
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
#  2. FSM bosqichlari
# ─────────────────────────────────────────────

@router.message(AddMod.waiting_name, F.chat.type == "private", F.from_user.id == ADMIN_ID)
async def step_name(message: types.Message, state: FSMContext):
    """Mod nomini qabul qiladi."""
    await state.update_data(name=message.text.strip())
    await message.answer("✅ Nom saqlandi!\n\nKategoriyani tanlang:", reply_markup=CATEGORY_KB)
    await state.set_state(AddMod.waiting_category)


@router.message(AddMod.waiting_category, F.chat.type == "private", F.from_user.id == ADMIN_ID)
async def step_category(message: types.Message, state: FSMContext):
    """Kategoriyani qabul qiladi."""
    cat = CATEGORY_MAP.get(message.text)
    if not cat:
        await message.answer("❗ Iltimos, tugmalar yordamida tanlang.")
        return
    await state.update_data(category=cat)
    await message.answer(
        "✅ Kategoriya saqlandi!\n\nMod <b>tavsifini</b> kiriting (1-3 jumla):",
        parse_mode="HTML",
        reply_markup=_cancel_kb(),
    )
    await state.set_state(AddMod.waiting_description)


@router.message(AddMod.waiting_description, F.chat.type == "private", F.from_user.id == ADMIN_ID)
async def step_description(message: types.Message, state: FSMContext):
    """Tavsifni qabul qiladi."""
    await state.update_data(description=message.text.strip())
    await message.answer(
        "✅ Tavsif saqlandi!\n\n"
        "💵 <b>Narxni</b> kiriting (UZS, faqat raqam):\n"
        "<i>Masalan: 49900</i>",
        parse_mode="HTML",
    )
    await state.set_state(AddMod.waiting_price)


@router.message(AddMod.waiting_price, F.chat.type == "private", F.from_user.id == ADMIN_ID)
async def step_price(message: types.Message, state: FSMContext):
    """Narxni qabul qiladi."""
    try:
        price = int(message.text.strip().replace(" ", "").replace(",", ""))
    except ValueError:
        await message.answer("❗ Faqat raqam kiriting. Masalan: <code>49900</code>", parse_mode="HTML")
        return
    await state.update_data(price=price)
    await message.answer(
        "✅ Narx saqlandi!\n\n"
        "🎬 <b>YouTube Shorts URL</b> kiriting:\n"
        "<i>Masalan: https://youtube.com/shorts/xxxxx</i>\n\n"
        "Yo'q bo'lsa <code>-</code> yuboring.",
        parse_mode="HTML",
    )
    await state.set_state(AddMod.waiting_video)


@router.message(AddMod.waiting_video, F.chat.type == "private", F.from_user.id == ADMIN_ID)
async def step_video(message: types.Message, state: FSMContext):
    """YouTube URL ni qabul qiladi."""
    video_url = "" if message.text.strip() == "-" else message.text.strip()
    await state.update_data(video_url=video_url)
    await message.answer(
        "✅ Video URL saqlandi!\n\n"
        "🖼️ <b>Thumbnail rasm URL</b> kiriting:\n"
        "Yo'q bo'lsa <code>-</code> yuboring.",
        parse_mode="HTML",
    )
    await state.set_state(AddMod.waiting_image)


@router.message(AddMod.waiting_image, F.chat.type == "private", F.from_user.id == ADMIN_ID)
async def step_image(message: types.Message, state: FSMContext):
    """Rasm URL ni qabul qiladi va bazaga saqlaydi."""
    image_url = "" if message.text.strip() == "-" else message.text.strip()
    data      = await state.get_data()
    await state.clear()

    # ── Bazaga yozish ────────────────────────────────────────
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
        f"🎉 <b>Mod qo'shildi!</b>\n\n"
        f"🆔 ID: <code>{mod_id}</code>\n"
        f"📛 Nom: <b>{data['name']}</b>\n"
        f"📂 Kategoriya: <b>{data['category']}</b>\n"
        f"💵 Narx: <b>{price_fmt} UZS</b>\n\n"
        f"Mod hozir /api/mods orqali chiqadi ✅",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )


# ─────────────────────────────────────────────
#  Admin buyruqlari
# ─────────────────────────────────────────────

@router.message(Command("admin"), F.from_user.id == ADMIN_ID)
async def cmd_admin(message: types.Message):
    mods = await db.get_all_mods()
    await message.answer(
        f"🛠️ <b>Admin Panel</b>\n\n"
        f"Bazadagi modlar: <b>{len(mods)} ta</b>\n\n"
        f"/listmods — Ro'yxat\n"
        f"/deletmod &lt;id&gt; — Modni o'chirish\n\n"
        f"📦 Yangi mod: faylni Arxiv guruhiga yuboring.",
        parse_mode="HTML",
    )


@router.message(Command("listmods"), F.from_user.id == ADMIN_ID)
async def cmd_listmods(message: types.Message):
    mods = await db.get_all_mods()
    if not mods:
        await message.answer("Hali mod yo'q.")
        return
    lines = [f"<b>Barcha modlar ({len(mods)} ta):</b>\n"]
    for m in mods:
        pf = f"{m['price']:,}".replace(",", " ")
        lines.append(f"🆔{m['id']} · {m['name']} · {m['category']} · {pf} UZS")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("deletemod"), F.from_user.id == ADMIN_ID)
async def cmd_deletemod(message: types.Message):
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Ishlatish: /deletemod &lt;mod_id&gt;", parse_mode="HTML")
        return
    deleted = await db.delete_mod(int(parts[1]))
    if deleted:
        await message.answer(f"✅ Mod #{parts[1]} o'chirildi.")
    else:
        await message.answer(f"❗ Mod #{parts[1]} topilmadi.")
