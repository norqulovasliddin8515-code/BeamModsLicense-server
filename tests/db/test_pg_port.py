import asyncio, os, sys, sqlite3, tempfile, subprocess
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),"..",".."))
sys.path.insert(0,ROOT)
DSN=os.environ["TEST_DATABASE_URL"]; os.environ["DATABASE_URL"]=DSN
os.environ.setdefault("BOT_TOKEN","x")
TMP=tempfile.mkdtemp(prefix="bms_pgtest_"); SQLITE=os.path.join(TMP,"beamods.db")
P=F=0
def ck(n,c):
    global P,F
    print(("  ok   " if c else "  FAIL ")+n); 
    P+=c; F+=(not c)

def build_sqlite():
    c=sqlite3.connect(SQLITE)
    c.executescript("""
      CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT NOT NULL, username TEXT,
        joined_at TEXT, subscription_tier TEXT DEFAULT 'free', subscription_expire TEXT, legacy_flag INTEGER);
      CREATE TABLE mods(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, category TEXT, description TEXT,
        price INTEGER, image_url TEXT, video_url TEXT, file_id TEXT, downloads INTEGER DEFAULT 0, created_at TEXT);
      CREATE TABLE orders(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, mod_id INTEGER, amount INTEGER,
        payment_method TEXT, status TEXT, transaction_id TEXT, created_at TEXT, paid_at TEXT);
      CREATE TABLE licenses(token TEXT PRIMARY KEY, user_id INTEGER, mod_key TEXT, hwid TEXT, status TEXT,
        created_at TEXT, bound_at TEXT, last_seen_at TEXT, last_ip TEXT, verify_count INTEGER, mismatch_count INTEGER, note TEXT);
      CREATE TABLE license_events(id INTEGER PRIMARY KEY AUTOINCREMENT, token TEXT, event TEXT, hwid TEXT, ip TEXT, at TEXT);
      CREATE TABLE payments(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, intent TEXT, amount_tiyin INTEGER,
        provider TEXT, status TEXT, provider_txn_id TEXT, payme_state INTEGER DEFAULT 0, payme_create_ms INTEGER,
        payme_perform_ms INTEGER, payme_cancel_ms INTEGER, payme_reason INTEGER, created_at TEXT, paid_at TEXT,
        fulfilled INTEGER DEFAULT 0, receipt_file_id TEXT, admin_msg_chat INTEGER, admin_msg_id INTEGER);
    """)
    c.execute("INSERT INTO users VALUES(1001,'Ali','ali','2026-01-01T00:00:00','pro','2099-01-01',7)")
    c.execute("INSERT INTO users VALUES(1002,'Vali',NULL,'2026-01-02T00:00:00','max','2020-01-01',0)")  # expired
    c.executemany("INSERT INTO mods(name,category,description,price,image_url,video_url,file_id,downloads,created_at) VALUES(?,?,?,?,?,?,?,?,?)",[
        ("CarA","cars","d",5000,"","","FILEA",3,"2026-02-01T00:00:00"),
        ("CarB","cars","d",9000,"","","FILEB",1,"2026-02-02T00:00:00")])
    c.execute("INSERT INTO orders(user_id,mod_id,amount,payment_method,status,transaction_id,created_at,paid_at) VALUES(1001,1,5000,'stars','paid','ch1','2026-03-01T00:00:00','2026-03-01T00:00:00')")
    c.execute("INSERT INTO licenses VALUES('bms_"+"a"*24+"',1001,'cara','HW','active','2026-03-01T00:00:00',NULL,NULL,NULL,0,0,'n')")
    c.execute("INSERT INTO license_events(token,event,hwid,ip,at) VALUES('bms_"+"a"*24+"','bind','HW','1.2.3.4','2026-03-01T00:00:00')")
    c.execute("INSERT INTO payments(user_id,intent,amount_tiyin,provider,status,provider_txn_id,payme_state,created_at,paid_at,fulfilled) VALUES(1001,'mod:1',500000,'stars','paid','ch1',0,'2026-03-01T00:00:00','2026-03-01T00:00:00',1)")
    c.execute("INSERT INTO payments(user_id,intent,amount_tiyin,provider,status,created_at) VALUES(1002,'sub:pro',2990000,'card','pending','2026-03-02T00:00:00')")
    c.commit(); c.close()

async def main():
    from bot import db as dbpool, database, config
    from bot.drm import licenses as L
    from bot.payments import core
    build_sqlite()
    # run migration as a subprocess (real usage)
    env=dict(os.environ, DATABASE_URL=DSN)
    r=subprocess.run([sys.executable, os.path.join(ROOT,"scripts","migrate_sqlite_to_pg.py"),
                      "--sqlite",SQLITE],capture_output=True,text=True,env=env)
    print(r.stdout.strip()); 
    if r.returncode: print(r.stderr[-800:])
    ck("migration exited 0", r.returncode==0)

    await dbpool.init_pool(DSN)
    # counts
    for t,exp in [("users",2),("mods",2),("orders",1),("licenses",1),("license_events",1),("payments",2)]:
        n=await dbpool.fetchval(f"SELECT COUNT(*) FROM {t}"); ck(f"{t} count={exp}", n==exp)
    # legacy_flag column was ignored (PG users has no such col)
    cols=[r["column_name"] for r in await dbpool.fetch("SELECT column_name FROM information_schema.columns WHERE table_name='users'")]
    ck("drift: legacy_flag not copied into PG", "legacy_flag" not in cols)

    # ported query correctness
    sub1=await database.get_subscription(1001); ck("sub active pro", sub1["tier"]=="pro" and sub1["is_active"])
    sub2=await database.get_subscription(1002); ck("sub expired -> free", sub2["tier"]=="free" and not sub2["is_active"])
    exp=await database.upgrade_subscription(2002,"max",30); ck("upgrade returns date", len(exp)==10)
    downs=await database.downgrade_expired_subscriptions(); ck("downgrade finds the expired max(1002)", 1002 in downs)
    ck("after downgrade 1002 is free", (await database.get_subscription(1002))["tier"]=="free")

    # sequence safety: add_mod must not collide with imported ids (1,2)
    newid=await database.add_mod("CarC","cars","d",1000,"","","FILEC"); ck("add_mod new id > 2", newid>2)
    allm=await database.get_all_mods(); ck("get_all_mods=3", len(allm)==3)
    one=await database.get_mod_by_id(1); ck("get_mod_by_id FILEA", one and one["file_id"]=="FILEA")
    ck("delete_mod ok", await database.delete_mod(newid))

    # orders / purchases (alias names handlers use)
    ck("has_purchased true", await database.has_purchased(1001,1))
    ck("has_user_purchased alias", await database.has_user_purchased(1001,1))
    ords=await database.get_user_purchases(1001); ck("get_user_purchases=1", len(ords)==1)

    # licenses: new issue + check_and_bind lifecycle
    tok=await L.issue_license(3003,"cara"); ck("issue_license", tok.startswith("bms_"))
    ck("bind first ok", await L.check_and_bind(tok,"cara","HWX","ip")=="ok")
    ck("same hwid ok", await L.check_and_bind(tok,"cara","HWX","ip")=="ok")
    ck("other hwid mismatch", await L.check_and_bind(tok,"cara","HWY","ip")=="hwid_mismatch")
    ck("wrong mod", await L.check_and_bind(tok,"other","HWX","ip")=="wrong_mod")
    await L.set_status(tok,"revoked"); ck("revoked", await L.check_and_bind(tok,"cara","HWX","ip")=="revoked")
    lic=await L.get_license(tok); ck("verify_count incremented", lic["verify_count"]>=2)

    # payments: create/mark_paid idempotent + fulfill-guard column exists
    p=await core.create_payment(4004,"sub:pro",29900,"card"); ck("create_payment id", p.id>2)
    ck("mark_paid wins once", await core.mark_paid(p.id,"card","T1"))
    ck("mark_paid again false", not await core.mark_paid(p.id,"card","T1"))
    ck("find_by_provider_txn", (await core.find_by_provider_txn("card","T1")).id==p.id)
    # payme dynamic state update
    await core.payme_set_state(p.id, 2, payme_perform_ms=123)
    gp=await core.get_payment(p.id); ck("payme_set_state", gp.payme_state==2)

    await dbpool.close_pool()
    print(f"\n{P} passed, {F} failed"); sys.exit(1 if F else 0)

asyncio.run(main())
