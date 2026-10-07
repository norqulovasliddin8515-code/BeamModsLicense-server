"""
End-to-end DRM test: real /verify server + real obfuscated bundle + LuaJIT with a BeamNG mock.
Run from the repo root (needs `luajit` and `lua-socket`):  python tests/drm/e2e.py
"""
import os, sys, asyncio, subprocess, shutil, json, time, re, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
W = tempfile.mkdtemp(prefix="bms_e2e_")
os.chdir(W)
KEY=f"{W}/drm.pem"
os.environ.update(BOT_TOKEN="123456:TESTTOKENtesttokenTESTTOKENtest12", OPENAI_API_KEY="x",
    DRM_SIGNING_KEY_FILE=KEY, PUBLIC_BASE_URL="https://127.0.0.1:18443", ADMIN_ID="42", DRM_TICKET_TTL_DAYS="7")
sys.path.insert(0, str(ROOT))
from bot.drm import keys
print("pubkey", keys.generate(KEY))
from aiohttp import web
from bot import database as db
from bot.drm import licenses, verify
from bot.drm.bundler import build_bundle, controller_name
import zipfile

# fake vehicle mod (with an existing controller section to test injection)
car=f"{W}/src/testcar"; os.makedirs(car)
open(f"{car}/testcar.jbeam","w").write('{"testcar": {"information":{"name":"Test"},\n "slotType" : "main",\n "controller": [["fileName"],["vehicleController",{}]],\n "nodes":[]}}\n')
open(f"{car}/other.jbeam","w").write('{"testcar_wheel": {"slotType":"wheel"}}')

alerts=[]
class FakeBot:
    async def send_message(self, chat, text, **kw): alerts.append(text)

def run_lua(lua, steam, scen="", extra=""):
    ctrl=controller_name(scen_mod[lua])
    r=subprocess.run(["luajit",str(HERE / "beamng_mock.lua"),lua,ctrl,steam,f"{W}/userfolder_{steam}.txt",extra],capture_output=True,text=True,timeout=60)
    out=(r.stdout+r.stderr).strip().splitlines()
    return out[-1] if out else "(no output)"
scen_mod={}

async def main():
    await db.init_db(); await licenses.init_drm_tables()
    app=web.Application(); app.router.add_get("/verify", verify.make_verify_handler(FakeBot(), 42))
    runner=web.AppRunner(app); await runner.setup()
    site=web.TCPSite(runner,"127.0.0.1",18443); await site.start()

    pub=keys.get_public_hex()
    tokA=await licenses.issue_license(1001,"testcar")
    zA=build_bundle(car,f"{W}/A.zip",token=tokA,mod_key="testcar",pubkey_hex=pub,base_url=os.environ["PUBLIC_BASE_URL"])
    tokA2=await licenses.issue_license(1002,"testcar")
    zA2=build_bundle(car,f"{W}/A2.zip",token=tokA2,mod_key="testcar",pubkey_hex=pub,base_url=os.environ["PUBLIC_BASE_URL"])
    tokB=await licenses.issue_license(1003,"othercar")   # license for a DIFFERENT mod
    zB=build_bundle(car,f"{W}/B.zip",token=tokB,mod_key="testcar",pubkey_hex=pub,base_url=os.environ["PUBLIC_BASE_URL"])

    def extract(z,name):
        d=f"{W}/x_{name}"; zipfile.ZipFile(z).extractall(d)
        lua=[os.path.join(r,f) for r,_,fs in os.walk(d) for f in fs if f.endswith(".lua")][0]
        scen_mod[lua]="testcar"; return d,lua
    dA,luaA=extract(zA,"A"); dA2,luaA2=extract(zA2,"A2"); dB,luaB=extract(zB,"B")

    # --- static checks
    src=open(luaA).read(); src2=open(luaA2).read()
    jb=open(f"{dA}/vehicles/testcar/testcar.jbeam").read()
    print("\n== static ==")
    print("zip files:", sorted(zipfile.ZipFile(zA).namelist()))
    print("jbeam controller rows:", re.findall(r'\["[^"]+"[^\]]*\]', jb.split('"controller"')[1].split('"nodes"')[0]))
    for needle,label in [(tokA,"token"),(pub,"pubkey"),("127.0.0.1","url"),("verify","'verify'"),("Steam","'Steam'"),("guihooks.message","guihooks.message"),("electrics","electrics"),("queueGameEngineLua","queueGameEngineLua"),("BMS1","'BMS1'")]:
        print(f"  plaintext {label:18s} present: {needle in src}")
    print("  size KB:", len(src)//1024, " two builds identical:", src==src2,
          " shared 40-char substrings:", len({src[i:i+40] for i in range(0,len(src)-40,40)} & {src2[i:i+40] for i in range(0,len(src2)-40,1)}))

    loop=asyncio.get_running_loop()
    async def L(*a): return await loop.run_in_executor(None, run_lua, *a)
    print("\n== dynamic ==")
    print("1 first launch (binds)         ", await L(luaA,"steam111"))
    print("2 relaunch same PC             ", await L(luaA,"steam111"))
    print("3 leaked zip on other PC       ", await L(luaA,"steam999"))
    print("   admin alert sent:", len(alerts)==1)
    print("4 other buyer's own copy       ", await L(luaA2,"steam222"))
    print("5 license of another mod       ", await L(luaB,"steam333"))
    print("6 console forge before answer  ", await L(luaA,"steam111","", "forge"))
    await site.stop()
    print("7 server down, valid cache     ", await L(luaA,"steam111"))
    print("8 server down, no cache        ", await L(luaA,"steam444"))
    # fake 'hosts' server: answers ok with junk signature / foreign key
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    evil=Ed25519PrivateKey.generate()
    async def fake(request):
        q=request.rel_url.query
        payload=f"BMS1|ok|{q['m']}|{q['t']}|{q['h']}|{q['n']}|{int(time.time())}|{int(time.time())+999999}"
        return web.Response(text=payload+"\n"+evil.sign(payload.encode()).hex())
    app2=web.Application(); app2.router.add_get("/verify",fake)
    r2=web.AppRunner(app2); await r2.setup(); s2=web.TCPSite(r2,"127.0.0.1",18443); await s2.start()
    print("9 fake server, foreign key     ", await L(luaA2,"steam555"))
    await s2.stop()
    # expired cache: rewrite store with old ticket signed by REAL key but expired
    import bot.drm.verify as V
    real_time=time.time
    V.time.time=lambda: real_time()-30*86400
    old=V.sign_payload("ok","testcar",tokA,"__H__","0"*32); V.time.time=real_time
    st=open(f"{W}/userfolder_steam111.txt").read()
    m=re.search(r'BMS1\|ok\|testcar\|[^|]+\|([0-9a-f]{64})\|',st)
    print("   (expired-cache rewrite applied:", bool(m), ")")
    if m:
        hw=m.group(1); old=old.replace("__H__",hw)
        payload=old.split("\n")[0]; sig=keys.get_signing_key().sign(payload.encode()).hex()
        expired=payload+"\n"+sig
        st=re.sub(r'\[("/settings/bms_[0-9a-f]+\.json")\]=\{t="[^"]*"', lambda mm: '['+mm.group(1)+']={t='+json.dumps(expired).replace("\\n","\\n")+'', st)
        open(f"{W}/userfolder_steam111.txt","w").write(st)
    print("10 server down, EXPIRED cache  ", await L(luaA,"steam111"))
    # revoke
    await site.start()
    await licenses.set_status(tokA2,"revoked")
    print("11 revoked license             ", await L(luaA2,"steam222"))
    await site.stop()
    print("12 revoked, then offline       ", await L(luaA2,"steam222"))
    lic=await licenses.get_license(tokA)
    print("\nDB tokA:", {k:lic[k] for k in ("status","verify_count","mismatch_count")}, "hwid bound:", bool(lic["hwid"]))
    await runner.cleanup()
    shutil.rmtree(W, ignore_errors=True)
asyncio.run(main())
