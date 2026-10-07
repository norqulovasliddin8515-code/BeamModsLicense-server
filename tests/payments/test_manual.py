"""
Manual card-transfer + admin-approval tests, incl. DRM-aware fulfillment.
Run:  python tests/payments/test_manual.py   (exit 0 = all pass)
"""
import asyncio
import os
import shutil
import sys
import tempfile
import zipfile

_TMP = tempfile.mkdtemp(prefix="bms_manual_")
os.environ.update(
    BOT_TOKEN="123:TEST", ADMIN_ID="42",
    CARD_NUMBER="8600 0000 0000 0000", CARD_HOLDER="TEST HOLDER",
    PUBLIC_BASE_URL="https://api.example.com",
    DRM_SIGNING_KEY_FILE=os.path.join(_TMP, "drm.pem"),
    DRM_MODS_DIR=os.path.join(_TMP, "mods"),
)
os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

import bot.database as db
from bot import db as dbpool

from bot.payments import core, manual
from bot.drm import keys as drm_keys

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ok   {name}")
    else:    FAIL += 1; print(f"  FAIL {name}")


SENT = {"docs": [], "photos": [], "messages": []}

class FakeBot:
    async def send_message(self, chat_id, text=None, **kw): SENT["messages"].append((chat_id, text)); return True
    async def send_photo(self, chat_id, photo=None, caption=None, reply_markup=None, **kw):
        SENT["photos"].append((chat_id, caption))
        return type("M", (), {"chat": type("C", (), {"id": chat_id})(), "message_id": 999})()
    async def send_document(self, chat_id=None, document=None, **kw):
        # DRM path uses FSInputFile; plain path (delivery) uses file_id string
        SENT["docs"].append((chat_id, type(document).__name__))
        return True

class FakeUser:
    def __init__(self, uid): self.id = uid; self.username = "buyer"; self.full_name = "Buyer One"

class FakeMsg:
    def __init__(self, uid):
        self.from_user = FakeUser(uid)
        self.photo = [type("P", (), {"file_id": "RECEIPT_FILEID"})()]
        self.replies = []
    async def answer(self, text, **kw): self.replies.append(text); return True

class FakeCbMsg:
    def __init__(self): self.caption = "orig"; self.text = None
    async def edit_caption(self, caption=None, reply_markup=None): self.caption = caption
    async def edit_reply_markup(self, reply_markup=None): pass

class FakeCb:
    def __init__(self, data, uid=42):
        self.data = data; self.from_user = FakeUser(uid); self.message = FakeCbMsg()
        self.answers = []
    async def answer(self, text=None, show_alert=False): self.answers.append(text)


async def seed_mod(price=5000, mod_key=None):
    mid = await db.add_mod("M", "cars", "d", price, "", "", "PLAINFILE")
    if mod_key:
        await db.set_mod_key(mid, mod_key)
    return mid


async def main():
    await dbpool.init_pool(os.environ["TEST_DATABASE_URL"])
    await dbpool.execute("DROP SCHEMA public CASCADE")
    await dbpool.execute("CREATE SCHEMA public")
    await db.init_db()
    await core.init_payment_tables()
    bot = FakeBot()

    # ---- patch plain delivery to detect fallback ----
    import bot.handlers.delivery as delivery
    PLAIN = []
    async def fake_plain(bot, user_id, mod_id): PLAIN.append((user_id, mod_id)); return True
    delivery.send_file_to_user = fake_plain

    mod_id = await seed_mod(price=5000)   # no mod_key -> plain delivery

    # ===== happy path: start -> receipt -> approve -> fulfilled once =====
    pay = await manual.start_card_payment(bot, 1001, f"mod:{mod_id}")
    check("start card -> pending", pay.status == "pending" and pay.provider == "card")
    check("card details DM sent", any("8600 0000 0000 0000" in (m[1] or "") for m in SENT["messages"]))

    msg = FakeMsg(1001)
    await manual.on_receipt(msg, bot)
    p = await core.get_payment(pay.id)
    check("receipt -> pending_manual_verification", p.status == "pending_manual_verification")
    check("receipt stored", p.receipt_file_id == "RECEIPT_FILEID")
    check("admin got receipt photo", any(ph[0] == 42 for ph in SENT["photos"]))
    check("user got ack", any("qabul qilindi" in r for r in msg.replies))

    # duplicate receipt should not re-notify (already in verify state)
    before = len(SENT["photos"])
    await manual.on_receipt(FakeMsg(1001), bot)
    check("duplicate receipt ignored", len(SENT["photos"]) == before)

    # approve -> fulfill once (plain delivery since no mod_key)
    cb = FakeCb(f"mapprove:{pay.id}")
    await manual.on_approve(cb, bot)
    p = await core.get_payment(pay.id)
    check("approve -> paid", p.status == "paid")
    check("plain delivered once", PLAIN.count((1001, mod_id)) == 1)
    check("admin msg marked approved", "APPROVED" in cb.message.caption)

    # double approve -> no second fulfillment
    cb2 = FakeCb(f"mapprove:{pay.id}")
    await manual.on_approve(cb2, bot)
    check("double approve blocked", any("Allaqachon" in (a or "") for a in cb2.answers))
    check("no double delivery", PLAIN.count((1001, mod_id)) == 1)

    # ===== reject path =====
    pay2 = await manual.start_card_payment(bot, 2002, f"mod:{mod_id}")
    await manual.on_receipt(FakeMsg(2002), bot)
    cbr = FakeCb(f"mreject:{pay2.id}")
    await manual.on_reject(cbr, bot)
    p2 = await core.get_payment(pay2.id)
    check("reject -> rejected", p2.status == "rejected")
    check("user told of rejection", any(m[0] == 2002 and "tasdiqlanmadi" in (m[1] or "") for m in SENT["messages"]))
    # approving a rejected one does nothing
    cbr2 = FakeCb(f"mapprove:{pay2.id}")
    await manual.on_approve(cbr2, bot)
    check("approve-after-reject blocked", (await core.get_payment(pay2.id)).status == "rejected")

    # non-admin cannot approve (filter is on the router; emulate by checking handler guards id)
    # (handlers are registered with F.from_user.id == ADMIN_ID, enforced by dispatcher)

    # ===== DRM-aware fulfillment: mod with a key + real bundle =====
    drm_keys.generate(os.environ["DRM_SIGNING_KEY_FILE"])
    src = os.path.join(os.environ["DRM_MODS_DIR"], "carmod")
    os.makedirs(src)
    with open(os.path.join(src, "carmod.jbeam"), "w") as f:
        f.write('{"carmod":{"slotType":"main","nodes":[]}}')
    drm_mod_id = await seed_mod(price=9000, mod_key="carmod")
    paid = await manual.start_card_payment(bot, 3003, f"mod:{drm_mod_id}")
    await manual.on_receipt(FakeMsg(3003), bot)
    docs_before = len(SENT["docs"])
    await manual.on_approve(FakeCb(f"mapprove:{paid.id}"), bot)
    check("DRM: a document was sent", len(SENT["docs"]) == docs_before + 1)
    check("DRM: sent via FSInputFile (bundle), not plain", SENT["docs"][-1][1] == "FSInputFile")
    check("DRM: plain delivery NOT used for keyed mod", (3003, drm_mod_id) not in PLAIN)
    # a license row was issued
    n = await dbpool.fetchval("SELECT COUNT(*) FROM licenses WHERE user_id=3003 AND mod_key='carmod'")
    check("DRM: license issued", n == 1)

    # ===== subscription via card =====
    psub = await manual.start_card_payment(bot, 4004, "sub:pro")
    await manual.on_receipt(FakeMsg(4004), bot)
    SUB = []
    orig = db.upgrade_subscription
    async def fake_sub(uid, tier, days=30): SUB.append((uid, tier)); return "2026-12-01"
    db.upgrade_subscription = fake_sub
    await manual.on_approve(FakeCb(f"mapprove:{psub.id}"), bot)
    check("sub via card activated", SUB.count((4004, "pro")) == 1)
    db.upgrade_subscription = orig

    await dbpool.close_pool()
    shutil.rmtree(_TMP, ignore_errors=True)
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


asyncio.run(main())
