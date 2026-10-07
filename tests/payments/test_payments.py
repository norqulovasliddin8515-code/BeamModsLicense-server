"""
End-to-end payment tests. Drives REAL aiohttp requests through the Click and
Payme handlers and real update objects through the Stars handlers, against a
temp SQLite DB. Fulfillment is counted to prove it runs exactly once.

Run:  python tests/payments/test_payments.py     (exit 0 = all pass)
"""
import asyncio
import base64
import hashlib
import os
import sys
import tempfile
import time

# ── env + temp DB BEFORE importing project modules ──
_TMP = tempfile.mkdtemp(prefix="bms_pay_")
os.environ.update(
    BOT_TOKEN="123:TEST", CLICK_SERVICE_ID="svc1", CLICK_MERCHANT_ID="mer1",
    CLICK_SECRET_KEY="click_secret", PAYME_MERCHANT_ID="pm1", PAYME_SECRET_KEY="payme_key",
    STARS_UZS_RATE="170",
)
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

import bot.database as db
db.DB_PATH = os.path.join(_TMP, "t.db")

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bot.payments import core, click, payme, stars

FULFILLED = []          # (kind, target, user) appended once per real fulfillment
SUBS = []

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ok   {name}")
    else:    FAIL += 1; print(f"  FAIL {name}")


class FakeBot:
    async def send_document(self, **kw): FULFILLED.append(("mod_doc", kw.get("chat_id"))); return True
    async def send_message(self, *a, **k): return True
    async def send_invoice(self, **kw): self.last_invoice = kw; return True


def md5(*p): return hashlib.md5("".join(p).encode()).hexdigest()


async def seed():
    await db.init_db()
    await core.init_payment_tables()
    # a priced mod (id will be whatever; grab first)
    async with __import__("aiosqlite").connect(db.DB_PATH) as c:
        await c.execute("INSERT INTO mods(name,category,price,file_id) VALUES ('T','cars',5000,'FILEID')")
        await c.commit()
        cur = await c.execute("SELECT id FROM mods ORDER BY id DESC LIMIT 1")
        return (await cur.fetchone())[0]


def build_app(bot):
    app = web.Application()
    app.router.add_post("/payment/click/prepare", click.prepare)
    app.router.add_post("/payment/click/complete", click.make_complete_handler(bot))
    app.router.add_post("/payment/payme", payme.make_payme_handler(bot))
    return app


async def main():
    bot = FakeBot()
    # patch fulfillment side-effects to count exactly-once
    import bot.handlers.delivery as delivery
    async def fake_deliver(bot, user_id, mod_id): FULFILLED.append(("mod", mod_id, user_id)); return True
    delivery.send_file_to_user = fake_deliver
    orig_sub = db.upgrade_subscription
    async def fake_sub(uid, tier, days=30): FULFILLED.append(("sub", tier, uid)); return "2026-12-01"
    db.upgrade_subscription = fake_sub

    mod_id = await seed()
    server = TestServer(build_app(bot)); client = TestClient(server); await client.start_server()

    # ===================== CLICK (mod purchase) =====================
    pay = await core.create_payment(1001, f"mod:{mod_id}", 5000)
    sid, ctid, stime, amt = "svc1", "ct1", "1700000000", "5000.00"
    def prep_sign(a=amt): return md5(ctid, sid, "click_secret", str(pay.id), a, "0", stime)
    # bad sign
    r = await client.post("/payment/click/prepare", data=dict(click_trans_id=ctid, service_id=sid,
        merchant_trans_id=str(pay.id), amount=amt, action="0", sign_time=stime, sign_string="deadbeef"))
    check("click prepare bad sign -> -1", (await r.json())["error"] == -1)
    # wrong amount
    r = await client.post("/payment/click/prepare", data=dict(click_trans_id=ctid, service_id=sid,
        merchant_trans_id=str(pay.id), amount="1.00", action="0", sign_time=stime, sign_string=prep_sign("1.00")))
    check("click prepare wrong amount -> -2", (await r.json())["error"] == -2)
    # valid prepare
    r = await client.post("/payment/click/prepare", data=dict(click_trans_id=ctid, service_id=sid,
        merchant_trans_id=str(pay.id), amount=amt, action="0", sign_time=stime, sign_string=prep_sign()))
    j = await r.json()
    check("click prepare ok -> 0", j["error"] == 0 and j["merchant_prepare_id"] == pay.id)

    prep_id = str(pay.id)
    def comp_sign(a=amt, err="0"):
        return md5(ctid, sid, "click_secret", str(pay.id), prep_id, a, "1", stime)
    # complete bad sign
    r = await client.post("/payment/click/complete", data=dict(click_trans_id=ctid, service_id=sid,
        merchant_trans_id=str(pay.id), merchant_prepare_id=prep_id, amount=amt, action="1",
        sign_time=stime, sign_string="bad", error="0"))
    check("click complete bad sign -> -1", (await r.json())["error"] == -1)
    # complete valid -> fulfill once
    r = await client.post("/payment/click/complete", data=dict(click_trans_id=ctid, service_id=sid,
        merchant_trans_id=str(pay.id), merchant_prepare_id=prep_id, amount=amt, action="1",
        sign_time=stime, sign_string=comp_sign(), error="0"))
    check("click complete ok -> 0", (await r.json())["error"] == 0)
    # complete retry -> still 0, no double fulfill
    r = await client.post("/payment/click/complete", data=dict(click_trans_id=ctid, service_id=sid,
        merchant_trans_id=str(pay.id), merchant_prepare_id=prep_id, amount=amt, action="1",
        sign_time=stime, sign_string=comp_sign(), error="0"))
    check("click complete retry -> 0", (await r.json())["error"] == 0)
    check("click delivered mod exactly once", FULFILLED.count(("mod", mod_id, 1001)) == 1)

    # forged complete on a brand-new unpaid payment with NO valid sign -> rejected
    pay2 = await core.create_payment(1002, f"mod:{mod_id}", 5000)
    r = await client.post("/payment/click/complete", data=dict(click_trans_id="x", service_id=sid,
        merchant_trans_id=str(pay2.id), merchant_prepare_id=str(pay2.id), amount="5000.00",
        action="1", sign_time=stime, sign_string="forged", error="0"))
    check("click forged complete rejected", (await r.json())["error"] == -1)
    check("forged did NOT fulfill", not any(f for f in FULFILLED if f[:2] == ("mod", mod_id) and f[2] == 1002))

    # ===================== PAYME (subscription) =====================
    psub = await core.create_payment(2001, "sub:pro", db.TIERS["pro"]["price_uzs"])
    amount_tiyin = db.TIERS["pro"]["price_uzs"] * 100
    good_auth = {"Authorization": "Basic " + base64.b64encode(b"Paycom:payme_key").decode()}
    bad_auth = {"Authorization": "Basic " + base64.b64encode(b"Paycom:wrong").decode()}

    async def rpc(method, params, headers=good_auth, rid=1):
        r = await client.post("/payment/payme", json={"jsonrpc": "2.0", "id": rid,
            "method": method, "params": params}, headers=headers)
        return (await r.json())

    acc = {"account": {"order_id": str(psub.id)}}
    # no auth
    j = await rpc("CheckPerformTransaction", {**acc, "amount": amount_tiyin}, headers={})
    check("payme no auth -> -32504", j["error"]["code"] == -32504)
    # wrong auth
    j = await rpc("CheckPerformTransaction", {**acc, "amount": amount_tiyin}, headers=bad_auth)
    check("payme wrong auth -> -32504", j["error"]["code"] == -32504)
    # wrong amount
    j = await rpc("CheckPerformTransaction", {**acc, "amount": 1})
    check("payme check wrong amount -> -31001", j["error"]["code"] == -31001)
    # valid check
    j = await rpc("CheckPerformTransaction", {**acc, "amount": amount_tiyin})
    check("payme check ok -> allow", j["result"]["allow"] is True)
    # create
    j = await rpc("CreateTransaction", {"id": "PTX1", "time": int(time.time()*1000),
        "amount": amount_tiyin, **acc})
    check("payme create -> state 1", j["result"]["state"] == 1 and j["result"]["transaction"] == str(psub.id))
    # create replay (idempotent)
    j = await rpc("CreateTransaction", {"id": "PTX1", "time": int(time.time()*1000),
        "amount": amount_tiyin, **acc})
    check("payme create replay -> state 1", j["result"]["state"] == 1)
    # perform
    j = await rpc("PerformTransaction", {"id": "PTX1"})
    check("payme perform -> state 2", j["result"]["state"] == 2)
    # perform replay -> no double fulfill
    j = await rpc("PerformTransaction", {"id": "PTX1"})
    check("payme perform replay -> state 2", j["result"]["state"] == 2)
    check("payme activated sub exactly once", FULFILLED.count(("sub", "pro", 2001)) == 1)
    # check transaction
    j = await rpc("CheckTransaction", {"id": "PTX1"})
    check("payme checktxn -> state 2", j["result"]["state"] == 2)
    # cancel after done -> -2
    j = await rpc("CancelTransaction", {"id": "PTX1", "reason": 5})
    check("payme cancel after done -> -2", j["result"]["state"] == -2)
    # unknown txn perform -> -31003
    j = await rpc("PerformTransaction", {"id": "NOPE"})
    check("payme perform unknown -> -31003", j["error"]["code"] == -31003)
    # unknown method
    j = await rpc("Frobnicate", {})
    check("payme unknown method -> -32601", j["error"]["code"] == -32601)
    # unknown order
    j = await rpc("CheckPerformTransaction", {"account": {"order_id": "999999"}, "amount": 1})
    check("payme unknown order -> -31050", j["error"]["code"] == -31050)

    # ===================== STARS =====================
    class PCQ:
        def __init__(self, payload): self.invoice_payload = payload; self.answered = None
        async def answer(self, ok, error_message=None): self.answered = ok
    pstar = await core.create_payment(3001, "sub:max", db.TIERS["max"]["price_uzs"], "stars")
    q = PCQ(f"pay:{pstar.id}"); await stars.on_pre_checkout(q)
    check("stars pre_checkout pending -> ok", q.answered is True)
    q2 = PCQ("pay:999999"); await stars.on_pre_checkout(q2)
    check("stars pre_checkout unknown -> reject", q2.answered is False)

    class SP:  telegram_payment_charge_id = "CHG1"; invoice_payload = f"pay:{pstar.id}"
    class Msg: successful_payment = SP()
    await stars.on_success(Msg(), bot)
    await stars.on_success(Msg(), bot)   # replay
    check("stars fulfilled sub exactly once", FULFILLED.count(("sub", "max", 3001)) == 1)

    # price derived from our list, never client
    check("stars price conversion", stars.stars_for_uzs(5000) == 30)  # ceil(5000/170)=30

    await client.close()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


asyncio.run(main())
