"""
admin.py — Admin uchun barcha handlerlar:
  1. Arxiv guruhida fayl aniqlash va file_id ni ushlab qolish.
  2. FSM orqali mod ma'lumotlarini kiritish (Nom, Kategoriya, Narx, YouTube URL).
  3. Ma'lumotni bazaga saqlash.
"""
import asyncio
from aiogram import Router, F, types, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)

from bot.config import ADMIN_ID, ARCHIVE_GROUP_ID
from bot import database as db

router = Router()


# ── FSM holatlari (Admin upload flow) ─────────────────────────────────────────

class ModUpload(StatesGroup):
    waiting_for_name        = State()
    waiting_for_category    = State()
    waiting_for_description = State()
    waiting_for_price       = State()
    waiting_for_youtube     = State()
    waiting_for_thumbnail   = State()


# ── Kategoriya tugmalari ───────────────────────────────────────────────────────

CATEGORY_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🚗 Cars"), KeyboardButton(text="🚛 Trucks")],
        [KeyboardButton(text="🗺️ Maps"), KeyboardButton(text="🎨 3D Models")],
        [KeyboardButton(text="❌ Bekor qilish")],
    ],
    resize_keyboard=True,
    one_time_keyboard=True,
)

CATEGORY_MAP = {
    "🚗 Cars": "cars",
    "🚛 Trucks": "trucks",
    "🗺️ Maps": "maps",
    "🎨 3D Models": "3d_models",
}


# ── Arxiv guruhida fayl aniqlash ──────────────────────────────────────────────

@router.message(
    F.chat.id == ARCHIVE_GROUP_ID,
    F.document | F.video | F.audio,
)
async def capture_file_id(message: types.Message, bot: Bot, state: FSMContext):
    """
    Arxiv guruhiga yuklangan ISTALGAN fayl (document/video/audio) ni ushlab oladi.
    file_id ni bazaga saqlamaydi — faqat Admin DM ga yuboradi va FSM ni boshlaydi.
    """
    # Faylni yuklab olmaymiz — faqat file_id ni olamiz
    file_obj = message.document or message.video or message.audio
    if not file_obj:
        return

    file_id   = file_obj.file_id
    file_name = getattr(file_obj, "file_name", "nomsiz_fayl")
    file_size = getattr(file_obj, "file_size", 0)
    size_mb   = round(file_size / (1024 * 1024), 1) if file_size else "?"

    # Admin ning DM siga xabar yuboramiz
    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"📦 <b>Yangi fayl aniqlandi!</b>\n\n"
                f"📄 Fayl nomi: <code>{file_name}</code>\n"
                f"💾 Hajmi: <b>{size_mb} MB</b>\n"
                f"🔑 File ID: <code>{file_id}</code>\n\n"
                f"Ushbu mod uchun ma'lumotlarni kiriting. "
                f"Ism yuboring 👇"
            ),
            parse_mode="HTML",
        )
        # FSM ni adminga bog'laymiz va file_id ni saqlaymiz
        # (state admin suhbatiga bog'liq, shuning uchun bot.get_state ishlatilmaydi)
        # Bu muammoni hal qilish uchun fayl ma'lumotini boshqacha saqlaymiz
        await state.set_state(ModUpload.waiting_for_name)
        await state.update_data(file_id=file_id, file_name=file_name)

    except Exception as e:
        print(f"Admin DM yuborishda xato: {e}")


# ── FSM: Mod ma'lumotlarini kiritish ─────────────────────────────────────────

@router.message(ModUpload.waiting_for_name, F.chat.type == "private", F.from_user.id == ADMIN_ID)
async def process_mod_name(message: types.Message, state: FSMContext):
    """Mod nomini qabul qiladi."""
    await state.update_data(name=message.text.strip())
    await message.answer(
        "✅ Nom saqlandi!\n\nKategoriyani tanlang:",
        reply_markup=CATEGORY_KEYBOARD,
    )
    await state.set_state(ModUpload.waiting_for_category)


@router.message(ModUpload.waiting_for_category, F.chat.type == "private", F.from_user.id == ADMIN_ID)
async def process_mod_category(message: types.Message, state: FSMContext):
    """Kategoriyani qabul qiladi."""
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("❌ Mod qo'shish bekor qilindi.", reply_markup=ReplyKeyboardRemove())
        return

    category = CATEGORY_MAP.get(message.text)
    if not category:
        await message.answer("❗ Iltimos, tugmalar yordamida kategoriya tanlang.")
        return

    await state.update_data(category=category)
    await message.answer(
        "✅ Kategoriya saqlandi!\n\n"
        "Mod tavsifini kiriting (qisqa, 1–3 jumla):",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(ModUpload.waiting_for_description)


@router.message(ModUpload.waiting_for_description, F.chat.type == "private", F.from_user.id == ADMIN_ID)
async def process_mod_description(message: types.Message, state: FSMContext):
    """Tavsifni qabul qiladi."""
    await state.update_data(description=message.text.strip())
    await message.answer(
        "✅ Tavsif saqlandi!\n\n"
        "💵 Narxni kiriting (UZS da, faqat raqam):\n"
        "<i>Masalan: 49900</i>",
        parse_mode="HTML",
    )
    await state.set_state(ModUpload.waiting_for_price)


@router.message(ModUpload.waiting_for_price, F.chat.type == "private", F.from_user.id == ADMIN_ID)
async def process_mod_price(message: types.Message, state: FSMContext):
    """Narxni qabul qiladi."""
    try:
        price = int(message.text.strip().replace(" ", "").replace(",", ""))
    except ValueError:
        await message.answer("❗ Faqat raqam kiriting. Masalan: <code>49900</code>", parse_mode="HTML")
        return

    await state.update_data(price=price)
    await message.answer(
        "✅ Narx saqlandi!\n\n"
        "🎬 YouTube Shorts URL ni kiriting:\n"
        "<i>Masalan: https://youtube.com/shorts/xxxxx</i>\n\n"
        "Yo'q bo'lsa <code>-</code> yuboring.",
        parse_mode="HTML",
    )
    await state.set_state(ModUpload.waiting_for_youtube)


@router.message(ModUpload.waiting_for_youtube, F.chat.type == "private", F.from_user.id == ADMIN_ID)
async def process_mod_youtube(message: types.Message, state: FSMContext):
    """YouTube URL ni qabul qiladi."""
    yt_url = "" if message.text.strip() == "-" else message.text.strip()
    await state.update_data(youtube_url=yt_url)
    await message.answer(
        "✅ YouTube URL saqlandi!\n\n"
        "🖼️ Thumbnail (rasm URL) kiriting yoki <code>-</code> yuboring:",
        parse_mode="HTML",
    )
    await state.set_state(ModUpload.waiting_for_thumbnail)


@router.message(ModUpload.waiting_for_thumbnail, F.chat.type == "private", F.from_user.id == ADMIN_ID)
async def process_mod_thumbnail(message: types.Message, state: FSMContext):
    """Thumbnail URL ni qabul qiladi va bazaga saqlaydi."""
    thumbnail = "" if message.text.strip() == "-" else message.text.strip()

    data = await state.get_data()
    await state.clear()

    # Bazaga yozish
    mod_id = await db.add_mod(
        name        = data["name"],
        category    = data["category"],
        description = data["description"],
        price       = data["price"],
        file_id     = data["file_id"],
        youtube_url = data.get("youtube_url", ""),
        thumbnail   = thumbnail,
    )

    price_formatted = f"{data['price']:,}".replace(",", " ")

    yt_text = data.get("youtube_url") or "Yo'q"

    await message.answer(
        f"🎉 <b>Mod muvaffaqiyatli qo'shildi!</b>\n\n"
        f"🆔 Mod ID: <code>{mod_id}</code>\n"
        f"📛 Nom: <b>{data['name']}</b>\n"
        f"📂 Kategoriya: <b>{data['category']}</b>\n"
        f"💵 Narx: <b>{price_formatted} UZS</b>\n"
        f"🎬 YouTube: {yt_text}\n\n"
        f"Mod katalogda ko'rinadi ✅",
        parse_mode="HTML",
    )


# ── Admin buyruqlari ──────────────────────────────────────────────────────────

@router.message(Command("admin"), F.from_user.id == ADMIN_ID)
async def admin_panel(message: types.Message):
    """Admin panel — asosiy buyruqlar."""
    await message.answer(
        "🛠️ <b>BeamModsStudio Admin Panel</b>\n\n"
        "Mavjud buyruqlar:\n"
        "/listmods — Barcha modlar ro'yxati\n"
        "/stats    — Statistika\n\n"
        "📦 Yangi mod qo'shish uchun faylni Arxiv guruhiga yuboring.",
        parse_mode="HTML",
    )


@router.message(Command("listmods"), F.from_user.id == ADMIN_ID)
async def list_mods(message: types.Message):
    """Barcha modlarni ko'rsatadi."""
    mods = await db.get_all_mods()
    if not mods:
        await message.answer("Hali mod qo'shilmagan.")
        return

    lines = [f"📦 <b>Barcha Modlar ({len(mods)} ta)</b>\n"]
    for m in mods:
        price_fmt = f"{m['price']:,}".replace(",", " ")
        lines.append(
            f"🆔 {m['id']} | {m['name']} | {m['category']} | {price_fmt} UZS"
        )

    await message.answer("\n".join(lines), parse_mode="HTML")
