"""
config.py — .env faylidan barcha sozlamalarni yuklaydi.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Telegram
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
ADMIN_ID: int = int(os.getenv("ADMIN_ID", "0"))
ARCHIVE_GROUP_ID: int = int(os.getenv("ARCHIVE_GROUP_ID", "0"))
WEB_APP_URL: str = os.getenv("WEB_APP_URL", "")

# OpenAI
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

# Click.uz
CLICK_SERVICE_ID: str = os.getenv("CLICK_SERVICE_ID", "")
CLICK_MERCHANT_ID: str = os.getenv("CLICK_MERCHANT_ID", "")
CLICK_SECRET_KEY: str = os.getenv("CLICK_SECRET_KEY", "")

# Payme
PAYME_MERCHANT_ID: str = os.getenv("PAYME_MERCHANT_ID", "")
PAYME_SECRET_KEY: str = os.getenv("PAYME_SECRET_KEY", "")

WEBHOOK_PORT: int = int(os.getenv("WEBHOOK_PORT", "8080"))
