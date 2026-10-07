"""
drm_admin.py — admin commands for DRM licenses (replaces the old python-telegram-bot bot.py).

  /deliver <user_id> <mod_key>   issue a license, build a personal bundle, send it to the buyer
  /license <token>               show license info
  /revoke <token>                block a license (leaker)
  /unrevoke <token>              re-activate a license
  /resetlicense <token>          unbind the HWID (buyer changed PC / Steam account)

Mod sources live in  <DRM_MODS_DIR>/<mod_key>/  (clean, unprotected copy).
"""
import html
import logging
import tempfile
from pathlib import Path

from aiogram import F, Router, types
from aiogram.filters import Command, CommandObject
from aiogram.types import FSInputFile

from bot.config import ADMIN_ID, DRM_MODS_DIR, PUBLIC_BASE_URL
from bot.drm import licenses
from bot.drm.bundler import build_bundle_async
from bot.drm.keys import get_public_hex

logger = logging.getLogger(__name__)
router = Router(name="drm_admin")
router.message.filter(F.from_user.id == ADMIN_ID, F.chat.type == "private")


def _arg_token(command: CommandObject) -> str | None:
    tok = (command.args or "").strip()
    return tok if licenses.TOKEN_RE.match(tok) else None


@router.message(Command("deliver"))
async def cmd_deliver(message: types.Message, command: CommandObject, bot):
    parts = (command.args or "").split()
    if len(parts) != 2 or not parts[0].isdigit() or not licenses.MOD_KEY_RE.match(parts[1]):
        await message.answer("Usage: <code>/deliver &lt;user_id&gt; &lt;mod_key&gt;</code>")
        return
    user_id, mod_key = int(parts[0]), parts[1]

    if not DRM_MODS_DIR:
        await message.answer("DRM_MODS_DIR is not set in .env")
        return
    source = (Path(DRM_MODS_DIR) / mod_key).resolve()
    if Path(DRM_MODS_DIR).resolve() not in source.parents or not source.is_dir():
        await message.answer(f"Mod source not found: <code>{html.escape(str(source))}</code>")
        return

    status = await message.answer("⚙️ Building personal bundle…")
    token = await licenses.issue_license(user_id, mod_key, note=f"deliver by {message.from_user.id}")
    try:
        with tempfile.TemporaryDirectory(prefix="bms_out_") as tmp:
            zip_path = Path(tmp) / f"{mod_key}.zip"
            await build_bundle_async(source, zip_path, token=token, mod_key=mod_key,
                                     pubkey_hex=get_public_hex(), base_url=PUBLIC_BASE_URL)
            await bot.send_document(
                user_id,
                FSInputFile(zip_path, filename=f"{mod_key}.zip"),
                caption=(
                    f"🎉 <b>{html.escape(mod_key)}</b>\n\n"
                    "Put this ZIP into your BeamNG <code>mods</code> folder.\n"
                    "The first launch needs internet to activate your license; "
                    "after that it works offline for up to 7 days at a time.\n\n"
                    "⚠️ This copy is personal and tied to your PC. Sharing it will deactivate it."
                ),
            )
    except Exception as e:
        logger.exception("[DRM] deliver failed")
        await licenses.set_status(token, "revoked")
        await status.edit_text(f"❌ Failed: <code>{html.escape(str(e))}</code>\nLicense voided.")
        return

    await status.edit_text(
        f"✅ Sent <b>{html.escape(mod_key)}</b> to <code>{user_id}</code>\nToken: <code>{token}</code>"
    )


@router.message(Command("license"))
async def cmd_license(message: types.Message, command: CommandObject):
    token = _arg_token(command)
    lic = await licenses.get_license(token) if token else None
    if not lic:
        await message.answer("License not found.")
        return
    await message.answer(
        f"<b>License</b> <code>{lic['token']}</code>\n"
        f"Mod: <code>{html.escape(lic['mod_key'])}</code>\n"
        f"Buyer: <a href=\"tg://user?id={lic['user_id']}\">{lic['user_id']}</a>\n"
        f"Status: <b>{lic['status']}</b>\n"
        f"HWID: <code>{(lic['hwid'] or 'not bound')[:24]}</code>\n"
        f"Created: {lic['created_at']}\nBound: {lic['bound_at'] or '-'}\n"
        f"Last seen: {lic['last_seen_at'] or '-'} ({lic['last_ip'] or '-'})\n"
        f"Verifications: {lic['verify_count']}  Mismatches: {lic['mismatch_count']}"
    )


@router.message(Command("revoke", "unrevoke"))
async def cmd_revoke(message: types.Message, command: CommandObject):
    token = _arg_token(command)
    new_status = "revoked" if command.command == "revoke" else "active"
    ok = token and await licenses.set_status(token, new_status)
    await message.answer(f"{'✅' if ok else '❌'} {new_status}: <code>{token or '?'}</code>")


@router.message(Command("resetlicense"))
async def cmd_reset(message: types.Message, command: CommandObject):
    token = _arg_token(command)
    ok = token and await licenses.reset_hwid(token)
    await message.answer(
        f"{'✅ HWID unbound — the next PC that starts the mod gets bound' if ok else '❌ not found'}: "
        f"<code>{token or '?'}</code>"
    )


@router.message(Command("setmodkey"))
async def cmd_setmodkey(message: types.Message, command: CommandObject):
    """/setmodkey <mod_id> <mod_key> — mark a catalog mod as DRM-protected.

    mod_key is the clean-source folder name under DRM_MODS_DIR. Once set, a paid
    purchase (Stars or approved card transfer) ships a per-buyer DRM bundle.
    """
    parts = (command.args or "").split()
    if len(parts) != 2 or not parts[0].isdigit() or not licenses.MOD_KEY_RE.match(parts[1]):
        await message.answer("Usage: <code>/setmodkey &lt;mod_id&gt; &lt;mod_key&gt;</code>")
        return
    mod_id, mod_key = int(parts[0]), parts[1]
    from bot import database as db_mod
    ok = await db_mod.set_mod_key(mod_id, mod_key)
    await message.answer(
        (f"✅ Mod #{mod_id} → DRM key <code>{mod_key}</code>.\n"
         f"Manba: <code>DRM_MODS_DIR/{mod_key}/</code> bo'lishi kerak." if ok
         else f"❌ Mod #{mod_id} topilmadi.")
    )
