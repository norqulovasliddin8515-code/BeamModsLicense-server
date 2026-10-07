"""
bundler.py — build a personalised, DRM-protected BeamNG vehicle mod zip.

Per buyer, per build:
  * a freshly obfuscated controller (random names + per-build string key)
  * the buyer's token, the mod key, the Ed25519 PUBLIC key and the /verify
    URLs live only inside encrypted strings (nothing grep-able)
  * the token is the leak watermark: a leaked zip identifies its buyer

CLI (on the VPS, from /opt/beammods/app):
    python -m bot.drm.bundler --source /opt/beammods/mod_sources/my_car \
        --mod-key my_car --user-id 123456789 --output /tmp/my_car_123.zip
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import re
import secrets
import shutil
import tempfile
import zipfile
from pathlib import Path

from bot.drm.obfuscator import obfuscate

LUA_DIR = Path(__file__).parent / "lua"
TELEGRAM_UPLOAD_LIMIT = 49 * 1024 * 1024   # Bot API sendDocument limit is 50 MB


def _h(s: str, n: int = 10) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:n]


def controller_name(mod_key: str) -> str:
    """Stable per mod (the .jbeam references it), unique across mods (no VFS clashes)."""
    return "bms_" + _h("ctrl:" + mod_key)


def _fill(template: str, values: dict[str, str]) -> str:
    for k, v in values.items():
        if any(ch in v for ch in ('"', "\\", "\n")):
            v = v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        template = template.replace(k, v)
    leftover = re.findall(r"__BMS_[A-Z_]+__", template)
    if leftover:
        raise ValueError(f"unfilled template placeholders: {leftover}")
    return template


def build_controller_source(*, token: str, mod_key: str, pubkey_hex: str, base_url: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", pubkey_hex):
        raise ValueError("pubkey_hex must be 64 hex chars")
    base_url = base_url.rstrip("/")
    if not base_url.startswith("https://"):
        raise ValueError("base_url must start with https://")
    crypto = (LUA_DIR / "crypto_fragment.lua").read_text(encoding="utf-8")
    ctrl = controller_name(mod_key)
    cb = "x" + secrets.token_hex(6)                         # random callback name per build
    common = {
        "__BMS_TOKEN__": token,
        "__BMS_MODKEY__": mod_key,
        "__BMS_CB__": cb,
    }
    ge_tpl = (LUA_DIR / "ge_template.lua").read_text(encoding="utf-8").replace("__BMS_CRYPTO__", crypto)
    ge_src = _fill(ge_tpl, {
        **common,
        "__BMS_URL_HTTPS__": base_url + "/verify",
        "__BMS_URL_HTTP__": "http://" + base_url[len("https://"):] + "/verify",
        "__BMS_CACHE_FILE__": f"/settings/bms_{_h('cache:' + mod_key)}.json",
        "__BMS_DEVICE_FILE__": "/settings/bms_device.json",
        "__BMS_CTRL__": ctrl,
        "__BMS_MEMO__": "bms_memo_" + _h("memo:" + mod_key),
    })
    ge_obf = obfuscate(ge_src)

    veh_src = (LUA_DIR / "controller_template.lua").read_text(encoding="utf-8")
    veh_src = veh_src.replace("__BMS_CRYPTO__", crypto)
    veh_src = _fill(veh_src, {**common, "__BMS_PUBKEY__": pubkey_hex, "__BMS_GE_CODE__": ge_obf})
    header = "-- BeamModsStudio licensed content. Redistribution is prohibited.\n"
    return header + obfuscate(veh_src)


# ── JBeam ─────────────────────────────────────────────────────────────────
_MAIN_SLOT_RE = re.compile(r'"slotType"\s*:\s*"main"\s*,?')
_CTRL_HEADER_RE = re.compile(r'"controller"\s*:\s*\[\s*\[\s*"fileName"[^\]]*\]\s*,?')


def inject_jbeam(vehicle_root: Path, ctrl: str) -> Path:
    for jb in sorted(vehicle_root.rglob("*.jbeam")):
        text = jb.read_text(encoding="utf-8", errors="strict")
        m = _MAIN_SLOT_RE.search(text)
        if not m:
            continue
        if f'"{ctrl}"' in text:
            return jb
        # The main part's keys sit between its slotType and the next part's slotType.
        nxt = text.find('"slotType"', m.end())
        window_end = nxt if nxt != -1 else len(text)
        cm = _CTRL_HEADER_RE.search(text, m.end(), window_end)
        if cm:   # main part already has controllers: add ours as a row
            text = text[:cm.end()] + f'\n        ["{ctrl}"],' + text[cm.end():]
        else:
            block = f'"slotType" : "main",\n    "controller": [\n        ["fileName"],\n        ["{ctrl}"]\n    ],'
            text = text[:m.start()] + block + text[m.end():]
        jb.write_text(text, encoding="utf-8")
        return jb
    raise RuntimeError("no .jbeam with \"slotType\": \"main\" found — is this a vehicle mod?")


# ── Bundle ────────────────────────────────────────────────────────────────
def build_bundle(source_dir: str | Path, output_zip: str | Path, *, token: str, mod_key: str,
                 pubkey_hex: str, base_url: str) -> Path:
    src = Path(source_dir).resolve()
    if not src.is_dir():
        raise FileNotFoundError(f"source dir not found: {src}")
    if not any(src.rglob("*.jbeam")):
        raise RuntimeError("DRM bundles are only supported for vehicle mods (.jbeam required)")

    out = Path(output_zip).resolve()
    ctrl = controller_name(mod_key)
    with tempfile.TemporaryDirectory(prefix="bms_bundle_") as tmp:
        root = Path(tmp)
        if (src / "vehicles").is_dir():          # already a mod root
            shutil.copytree(src, root, dirs_exist_ok=True)
            vehicle_dirs = [d for d in (root / "vehicles").iterdir() if d.is_dir()]
        else:                                     # a bare vehicle folder
            dest = root / "vehicles" / src.name
            shutil.copytree(src, dest)
            vehicle_dirs = [dest]
        if len(vehicle_dirs) != 1:
            raise RuntimeError(f"expected exactly one vehicle folder, found {len(vehicle_dirs)}")
        vdir = vehicle_dirs[0]

        for stale in vdir.rglob("drm_controller.lua"):   # old unsigned controller
            stale.unlink()
        inject_jbeam(vdir, ctrl)

        lua_path = vdir / "lua" / "controller" / f"{ctrl}.lua"
        lua_path.parent.mkdir(parents=True, exist_ok=True)
        lua_path.write_text(
            build_controller_source(token=token, mod_key=mod_key, pubkey_hex=pubkey_hex, base_url=base_url),
            encoding="utf-8", newline="\n",
        )

        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(root).as_posix())

    if out.stat().st_size > TELEGRAM_UPLOAD_LIMIT:
        raise RuntimeError(f"bundle is {out.stat().st_size >> 20} MB — over Telegram's 50 MB bot upload limit")
    return out


async def build_bundle_async(*args, **kwargs) -> Path:
    return await asyncio.to_thread(build_bundle, *args, **kwargs)


if __name__ == "__main__":
    from bot.config import PUBLIC_BASE_URL
    from bot.drm.keys import get_public_hex
    from bot.drm import licenses

    ap = argparse.ArgumentParser(description="Build a personalised DRM bundle")
    ap.add_argument("--source", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--mod-key", required=True, help="stable id of the mod, e.g. bmw_m5_f90")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--user-id", type=int, help="issue a NEW license for this Telegram user")
    g.add_argument("--token", help="rebuild for an existing license token")
    a = ap.parse_args()

    async def _main():
        await licenses.init_drm_tables()
        token = a.token or await licenses.issue_license(a.user_id, a.mod_key, note="cli")
        path = build_bundle(a.source, a.output, token=token, mod_key=a.mod_key,
                            pubkey_hex=get_public_hex(), base_url=PUBLIC_BASE_URL)
        print(f"OK {path}  token={token}")

    asyncio.run(_main())
