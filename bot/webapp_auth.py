"""
webapp_auth.py — Telegram Mini App initData verification.

Never trust a user_id sent in a JSON body: anyone can POST any number.
The Mini App gets `Telegram.WebApp.initData`, a query string signed by
Telegram with a key derived from our BOT_TOKEN. We verify the HMAC and
take the user id from inside it.

Docs: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""
import time
from typing import Optional

from aiogram.utils.web_app import WebAppInitData, safe_parse_webapp_init_data

from bot.config import BOT_TOKEN, INIT_DATA_MAX_AGE


class InitDataError(Exception):
    """initData is missing, forged or expired."""


def verify_init_data(init_data: Optional[str]) -> WebAppInitData:
    """
    Validate the signature and age of Mini App initData.
    Returns the parsed data (use `.user.id`). Raises InitDataError.
    """
    if not init_data or not isinstance(init_data, str):
        raise InitDataError("init_data missing")

    try:
        data = safe_parse_webapp_init_data(token=BOT_TOKEN, init_data=init_data)
    except ValueError:
        raise InitDataError("bad signature")

    auth_ts = int(data.auth_date.timestamp())
    if time.time() - auth_ts > INIT_DATA_MAX_AGE:
        raise InitDataError("init_data expired")

    if not data.user or not data.user.id:
        raise InitDataError("no user in init_data")

    return data
