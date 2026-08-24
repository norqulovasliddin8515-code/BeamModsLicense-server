"""
ai_assistant.py — OpenAI GPT-4o-mini yordamida mod maslahatchi va
o'rnatish qo'llanmasi beruvchi AI handler.

Xususiyatlar:
  - Bazadagi modlar katalogini kontekst sifatida yuboradi (Semantic search)
  - "Offroad truck" degan so'rovga eng mos modni tavsiya qiladi
  - O'rnatish bo'yicha savollar (masalan "modni qanday o'rnataman?") ga javob beradi
  - Anti-spam: foydalanuvchi boshqacha savol berguncha suhbat davom etadi
"""
from aiogram import Router, F, types
from aiogram.filters import Command
from openai import AsyncOpenAI

from bot.config import OPENAI_API_KEY
from bot import database as db

router = Router()
client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# Har bir foydalanuvchi uchun suhbat tarixi (xotira)
_chat_history: dict[int, list[dict]] = {}


# ── Tizim xabari (System Prompt) ──────────────────────────────────────────────

SYSTEM_PROMPT = """
Siz BeamModsStudio do'konining AI yordamchisisiz.
Do'kon BeamNG.drive o'yini uchun premium modlar, xaritalar va 3D modellar sotadi.

Vazifalaringiz:
1. Foydalanuvchi so'roviga eng mos mod(lar) ni tavsiya qiling.
2. BeamNG.drive ga mod o'rnatish bo'yicha qo'llanma bering.
3. Narx va kategoriyalar haqida ma'lumot bering.

QOIDALAR:
- Faqat shu do'kon mahsulotlari haqida gapiring.
- O'zbek tilida javob bering (agar so'rov o'zbekcha bo'lsa).
- Javobni qisqa va aniq qiling (3-5 jumla).
- Mod tavsiya qilganingizda narxni ham ayting.

MAVJUD MODLAR KATALOGI:
{catalog}
"""


async def _get_catalog_context() -> str:
    """Bazadan modlarni o'qib, AI uchun matn kontekst tayyorlaydi."""
    mods = await db.get_all_mods()
    if not mods:
        return "Hozircha modlar mavjud emas."

    lines = []
    for m in mods:
        price_fmt = f"{m['price']:,}".replace(",", " ")
        desc = m.get('description') or "Mavjud emas"
        lines.append(
            f"- [{m['id']}] {m['name']} | {m['category']} | {price_fmt} UZS\n"
            f"  Tavsif: {desc}"
        )
    return "\n".join(lines)


# ── /ai buyrug'i ──────────────────────────────────────────────────────────────

@router.message(Command("ai"))
async def cmd_ai(message: types.Message):
    """AI yordamchini ishga tushiradi."""
    user_id = message.from_user.id
    _chat_history[user_id] = []  # Yangi suhbat boshlash

    await message.answer(
        "🤖 <b>BeamModsStudio AI Yordamchi</b>\n\n"
        "Salom! Men sizga mod tanlash va o'rnatish bo'yicha yordam bera olaman.\n\n"
        "Masalan:\n"
        "• <i>«Menga offroad flatbed truck kerak»</i>\n"
        "• <i>«Eng arzon xaritani tavsiya qil»</i>\n"
        "• <i>«Modni qanday o'rnataman?»</i>\n\n"
        "Savolingizni yozing 👇\n"
        "<i>Suhbatni tugatish: /stop</i>",
        parse_mode="HTML",
    )


@router.message(Command("stop"))
async def cmd_stop(message: types.Message):
    """AI suhbatini tugatadi."""
    _chat_history.pop(message.from_user.id, None)
    await message.answer("✅ AI suhbati tugatildi. Yangi suhbat boshlash: /ai")


# ── AI savol-javob handler ────────────────────────────────────────────────────

@router.message(F.text & F.chat.type == "private")
async def ai_chat_handler(message: types.Message):
    """
    Foydalanuvchi private chatda yozganida AI ga yuboradi.
    Faqat /ai yuborgandan keyin faollashadi (chat_history mavjud bo'lsa).
    """
    user_id = message.from_user.id

    # AI faol emasmi?
    if user_id not in _chat_history:
        return  # Boshqa handlerga uzatamiz

    user_text = message.text.strip()

    # Typing indikatori
    await message.bot.send_chat_action(message.chat.id, "typing")

    # Katalog kontekstini yangilash
    catalog = await _get_catalog_context()
    system  = SYSTEM_PROMPT.format(catalog=catalog)

    # Suhbat tarixiga qo'shish
    history = _chat_history[user_id]
    history.append({"role": "user", "content": user_text})

    # Suhbat juda uzun bo'lsa kesish (token limit uchun)
    if len(history) > 10:
        history = history[-10:]
        _chat_history[user_id] = history

    try:
        response = await client.chat.completions.create(
            model       = "gpt-4o-mini",
            messages    = [{"role": "system", "content": system}] + history,
            max_tokens  = 500,
            temperature = 0.7,
        )
        ai_reply = response.choices[0].message.content.strip()

        # AI javobini tarixga qo'shish
        history.append({"role": "assistant", "content": ai_reply})

        await message.answer(
            f"🤖 {ai_reply}\n\n<i>Davom etish uchun savol bering | /stop — tugatish</i>",
            parse_mode="HTML",
        )

    except Exception as e:
        print(f"OpenAI xatosi: {e}")
        await message.answer(
            "❗ AI xizmati hozircha mavjud emas. Keyinroq urinib ko'ring.",
        )
