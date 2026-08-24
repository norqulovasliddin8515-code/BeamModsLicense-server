# BeamModsStudio — MVP v1.0 Setup & Run Guide

## Loyiha tuzilmasi

```
c:/beammodsstudio/
├── bot/
│   ├── __init__.py
│   ├── main.py              ← Bot entry point
│   ├── config.py            ← .env loader
│   ├── database.py          ← SQLite CRUD
│   └── handlers/
│       ├── admin.py         ← File_id capture + FSM upload flow
│       ├── user.py          ← /start, web_app_data handler
│       ├── payment.py       ← Click.uz + Payme webhooks
│       ├── delivery.py      ← Auto file delivery (file_id)
│       └── ai_assistant.py  ← OpenAI GPT-4o-mini AI yordamchi
├── webapp/
│   └── index.html           ← TWA (Catalog + Shorts Feed)
├── .env.example
├── requirements.txt
└── README.md
```

## 1. O'rnatish

```bash
# Python virtual muhit yaratish
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # Mac/Linux

# Kutubxonalar o'rnatish
pip install -r requirements.txt
```

## 2. .env faylini yaratish

```bash
copy .env.example .env
# Keyin .env faylini oching va barcha kalitlarni kiriting:
# - BOT_TOKEN: BotFather dan
# - ADMIN_ID: @userinfobot orqali oling
# - ARCHIVE_GROUP_ID: Arxiv guruhning ID si (manfiy son)
# - WEB_APP_URL: index.html ning HTTPS manzili
# - OPENAI_API_KEY: platform.openai.com dan
# - CLICK_SERVICE_ID, CLICK_MERCHANT_ID, CLICK_SECRET_KEY
# - PAYME_MERCHANT_ID, PAYME_SECRET_KEY
```

## 3. Web App (index.html) ni host qilish

> Telegram Web App HTTPS talab qiladi!

```bash
# Mahalliy server (development uchun)
cd webapp
python -m http.server 8000

# Ngrok bilan HTTPS tunnel
ngrok http 8000
# Ngrok sizga: https://xxxx.ngrok-free.app beradi
# Bu URL ni .env da WEB_APP_URL ga kiriting
```

## 4. Telegram Bot sozlamalari (BotFather)

```
/mybots → Botingizni tanlang → Bot Settings →
  Menu Button → Configure menu button →
  URL: https://xxxx.ngrok-free.app/index.html
  Text: 🏪 BeamModsStudio

Domain: Allowed → Web App domain ga ngrok URL qo'shing
```

## 5. Arxiv Guruhini sozlash

1. Telegram da yangi **Private Group** yarating: "BeamMods Archive"
2. Botingizni guruhga **Admin** sifatida qo'shing
3. `/start` ni guruhda yuboring va guruh ID sini oling
4. Bu ID ni `.env` da `ARCHIVE_GROUP_ID` ga kiriting

## 6. Botni ishga tushirish

```bash
python -m bot.main
```

## 7. Admin Mod Upload Workflow

```
1. Arxiv guruhiga fayl yuboring (max 2GB) ✅
2. Bot avtomatik file_id ni ushlab oladi    ✅
3. Bot sizning DM ingizga xabar yuboradi    ✅
4. Navbat bilan kiritasiz:
   → Mod nomi
   → Kategoriya (tugmalar orqali)
   → Tavsif
   → Narx (UZS da)
   → YouTube Shorts URL
   → Thumbnail URL
5. Mod katalogda paydo bo'ladi              ✅
```

## 8. To'lov Webhooklar (Click.uz va Payme)

```
Click PREPARE : POST /payment/click/prepare
Click COMPLETE: POST /payment/click/complete
Payme         : POST /payment/payme

Webhook URL formatı: https://yourdomain.com/payment/click/prepare
```

> Production da Ngrok o'rniga real domen va SSL sertifikat kerak!

## 9. AI Yordamchi (Foydalanuvchi uchun)

```
Botda /ai yozing
Keyin istalgan savolni yozing:
  "Menga offroad flatbed truck kerak"
  "Eng arzon xaritani ko'rsat"
  "BeamNG ga mod qanday o'rnatiladi?"
```

## Arxitektura diagrammasi

```
[Foydalanuvchi]
     │
     ├─ TWA ochadi ──────────────────────────→ [index.html Catalog+Shorts]
     │                                                  │
     │                                           "Buy" bosiladi
     │                                                  │
     └─ Bot DM ←─────────────────────────── tg.sendData(buy_mod)
              │
              ├─ To'lov URL yuboradi (Click/Payme)
              │         │
              │   [Foydalanuvchi to'laydi]
              │         │
              │   Webhook keladi ──────────── mark_order_paid()
              │                                      │
              └─ bot.send_document(file_id=...) ────→ [Fayl DM da yetib keladi]
                  (Hech qanday yuklab olish yo'q — 0 bandwidth!)
```
