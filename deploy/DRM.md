# Phase 2 — DRM: Ed25519-signed licenses + per-buyer obfuscation

## How it works now

```
 BeamNG (buyer's PC)                                      VPS
 ┌─────────────────────────────────────────┐
 │ vehicle Lua: bms_<id>.lua (controller)  │
 │  1. locks engine/brakes every frame     │
 │  2. makes a random nonce                │
 │  3. hands GE code + nonce to GE ───────┐│
 │                                        ││
 │ GE Lua (same file, encrypted inside)   ││   GET /verify?t=token&m=mod&h=hwid&n=nonce
 │  4. hwid = H(Steam account)  ──────────┼┼──────────────────────────► aiohttp
 │  5. HTTPS, else HTTP ◄─────────────────┼┼──── "BMS1|ok|mod|token|hwid|nonce|iat|exp"
 │  6. passes raw text back to vehicle    ││     + Ed25519 signature (private key on VPS only)
 │                                        ▼│
 │  7. vehicle Lua verifies the signature with the PUBLIC key,
 │     checks mod/token/hwid/nonce → unlock. Otherwise stay locked.
 │  8. Offline: a signed "ok" ticket cached ≤ 7 days is accepted
 └─────────────────────────────────────────┘
```

### What each attack now runs into (all tested in `tests/drm/e2e.py`)

| Attack | Before | Now |
|---|---|---|
| Fake server through the `hosts` file | worked: `{"valid":true}` unlocked | ❌ no private key, so the signature fails |
| Forge the offline cache file | worked: the SHA-256 "secret" was inside the Lua | ❌ needs a signature from the private key |
| Replay a recorded server answer | worked | ❌ the nonce is new on every spawn; old cached tickets expire after 7 days |
| Console: `setVerificationStatus(true)` | worked: one line unlocked it | ❌ the function is gone; the callback demands a valid signature |
| `_G.drm_verifiedTokens[token]=true` | worked | ❌ removed |
| Share zip + `device_profile.json` | worked: HWID came from the copied file | ❌ HWID comes from the Steam account; the file is only a fallback |
| Cheap mod's token unlocks an expensive mod | possible | ❌ `mod_key` is signed and checked |
| `grep` the zip for URL, token, key | trivial | ❌ every string is encrypted with a per-build key |
| One crack script that works on every copy | yes | harder: random names and keys per buyer |
| Delete the controller row from the `.jbeam`, or rewrite the Lua | works | **still works** (see below) |

### The honest limit

Everything runs on the buyer's PC, and they can edit any file there. Someone determined
can remove the controller from the JBeam or patch the Lua. No Lua obfuscator prevents
that. Neither does Denuvo for native games. The realistic goal, which this now meets:

1. Casual sharing doesn't work. The zip alone does nothing on another PC.
2. Cracking takes real effort per copy, and there's no universal patch.
3. Every leaked copy names its buyer. The token is inside, and the bot alerts you with the
   buyer's Telegram ID the moment a second PC tries it. You can `/revoke` the token and ban the buyer.

Point 3 is your strongest weapon. Keep the alert flow fast.

---

## Setup on the VPS (after Phase 1)

```bash
sudo -u beammods mkdir -p /opt/beammods/keys /opt/beammods/mod_sources
sudo -u beammods /opt/beammods/venv/bin/python -m bot.drm.keys generate /opt/beammods/keys/drm_ed25519.pem
#  -> prints PUBLIC KEY: ab12...   (it's also logged at every bot start)
```

**Back up `drm_ed25519.pem` offline** (password manager or encrypted USB) and never commit it.
`.gitignore` now blocks `*.pem`.
- Key **lost** → every bundle already sold must be rebuilt and re-sent.
- Key **leaked** → anyone can sign "ok". Generate a new key and rebuild all bundles.

Add to `/opt/beammods/app/.env`:

```
DRM_SIGNING_KEY_FILE=/opt/beammods/keys/drm_ed25519.pem
DRM_MODS_DIR=/opt/beammods/mod_sources
DRM_TICKET_TTL_DAYS=7
```

Upload the clean mod sources (unpacked, unprotected) from Windows:

```powershell
scp -r "C:\path\to\UniversalAttachSystem" root@<VPS_IP>:/opt/beammods/mod_sources/uas
ssh root@<VPS_IP> chown -R beammods:beammods /opt/beammods/mod_sources
```

The folder name is the **mod_key** (`uas` above): letters, digits, `_ . -`.
Accepted layouts: a bare vehicle folder with `.jbeam` files, or a mod root containing `vehicles/<name>/`.

Deploy the code and the new Nginx config:

```bash
sudo bash /opt/beammods/app/deploy/update.sh
sed "s/api.example.com/api.YOURDOMAIN/g" /opt/beammods/app/deploy/nginx/beammods.conf \
  | sudo tee /etc/nginx/sites-available/beammods.conf >/dev/null
sudo nginx -t && sudo systemctl reload nginx
curl "http://api.YOURDOMAIN/verify?t=x"      # -> "bad request" (400) = route is live
```

## Daily use (admin chat with the bot)

| Command | What it does |
|---|---|
| `/deliver 123456789 uas` | new license, freshly obfuscated personal zip sent to that user |
| `/license bms_…` | buyer, HWID, last seen, mismatches |
| `/revoke bms_…` / `/unrevoke bms_…` | block or unblock (takes effect on the next online launch; at most 7 days offline) |
| `/resetlicense bms_…` | buyer changed Steam account or PC: unbind so the next launch re-binds |

A leak alert looks like `🚨 Possible leak … Buyer: 123456789` with these commands ready to tap.

CLI alternative: `python -m bot.drm.bundler --source … --mod-key uas --user-id 123 --output /tmp/x.zip`

The old `bot.py` (python-telegram-bot) and FastAPI `app.py` are replaced by this. Retire them.
Bundles built with the old `drm_controller.lua` point to `127.0.0.1:8000` and must be rebuilt.

## ⚠️ Test in the real game before selling (I could only test with a mock)

The Lua was tested on LuaJIT 2.1, the same engine BeamNG uses, with BeamNG's GE and vehicle
environments mocked. Check these in-game with one `/deliver` to yourself:

1. **Steam ID API.** In the GE console (`~`), run each line:
   ```lua
   print(Steam and Steam.getAccountID and Steam.getAccountID())
   print(Steam and Steam.getUserId64 and Steam.getUserId64())
   print(Engine and Engine.Steam and Engine.Steam.getAccountID and Engine.Steam.getAccountID())
   ```
   The controller tries all of them. If **none** prints a number, the HWID falls back to
   `settings/bms_device.json`, which can be copied with a leak. Tell me what prints and I'll adjust.
2. **Vehicle spawns locked, then unlocks** within about a second. The ignition comes back.
3. **`require("bit")` in vehicle Lua.** If the car drives with no license check at all, the
   controller failed to load. Look for `bms_` errors in `beamng.log`. That would fail *open*, so this
   test matters most.
4. **Offline:** disconnect the internet and respawn. It should still unlock (cached ticket).
5. **Leak test:** copy the zip to a second PC or Steam account. It should lock, and you should get the 🚨 alert.

## Limits and next steps

- The Telegram bot API upload limit is **50 MB**. `/deliver` refuses bigger bundles. For big mods the
  next step is to split them into a shared base mod (sent once by `file_id`) plus a tiny personal
  license pack. Ask when you need it.
- Each launch makes one blocking HTTP call, up to 4 s if the server is down. That happens at most
  once per 10 minutes when offline.
- Verifying a signature costs about 20–40 ms per vehicle spawn.
