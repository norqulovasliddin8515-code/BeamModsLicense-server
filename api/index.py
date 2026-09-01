from http.server import BaseHTTPRequestHandler
import json
import os
import urllib.request
import urllib.parse

BOT_TOKEN = os.getenv("BOT_TOKEN", "8665911741:AAHg-R9XtJjDVaGxX6H5eG7AB8koA95YH3g")

class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, ngrok-skip-browser-warning')
        self.end_headers()

    def do_GET(self):
        # CORS headers
        self.send_response(200)
        self.send_header('Content-type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, ngrok-skip-browser-warning')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.end_headers()

        # mods.json faylini qidiramiz
        mods_file = os.path.join(os.path.dirname(__file__), '..', 'mods.json')
        if not os.path.exists(mods_file):
            mods_file = os.path.join(os.getcwd(), 'mods.json')

        if os.path.exists(mods_file):
            with open(mods_file, 'r', encoding='utf-8') as f:
                content = f.read()
            self.wfile.write(content.encode('utf-8'))
        else:
            self.wfile.write(json.dumps({
                "ok": True,
                "count": 0,
                "mods": []
            }).encode('utf-8'))

    def do_POST(self):
        # CORS headers
        self.send_response(200)
        self.send_header('Content-type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, ngrok-skip-browser-warning')
        self.end_headers()

        length = int(self.headers.get('content-length', 0))
        body = {}
        if length > 0:
            try:
                body = json.loads(self.rfile.read(length).decode('utf-8'))
            except Exception:
                pass

        mod_id = body.get('mod_id')
        user_id = body.get('user_id')

        if not mod_id or not user_id:
            self.wfile.write(json.dumps({"ok": False, "error": "mod_id va user_id kerak"}).encode('utf-8'))
            return

        try:
            mod_id = int(mod_id)
            user_id = int(user_id)
        except (ValueError, TypeError):
            self.wfile.write(json.dumps({"ok": False, "error": "Noto'g'ri parametrlar"}).encode('utf-8'))
            return

        # mods.json faylidan modni topish
        mods_file = os.path.join(os.path.dirname(__file__), '..', 'mods.json')
        if not os.path.exists(mods_file):
            mods_file = os.path.join(os.getcwd(), 'mods.json')

        target_mod = None
        if os.path.exists(mods_file):
            try:
                with open(mods_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for m in data.get('mods', []):
                        if m.get('id') == mod_id:
                            target_mod = m
                            break
            except Exception:
                pass

        if not target_mod:
            self.wfile.write(json.dumps({"ok": False, "error": "Mod topilmadi"}).encode('utf-8'))
            return

        file_id = target_mod.get('file_id', '')
        if not file_id or file_id.startswith('demo_'):
            self.wfile.write(json.dumps({"ok": False, "error": "demo", "name": target_mod.get('name')}).encode('utf-8'))
            return

        # Telegram Bot API orqali yuborish
        try:
            price = target_mod.get('price', 0)
            price_str = f"{price:,}".replace(",", " ") + " UZS" if price else "Bepul"
            desc = target_mod.get('description', '') or ''
            caption = (
                f"<b>{target_mod['name']}</b>\n"
                f"Kategoriya: {target_mod.get('category', '')}   |   {price_str}\n\n"
                f"{desc}\n\n"
                f"BeamModsStudio orqali yuklandi"
            )

            payload = urllib.parse.urlencode({
                'chat_id': user_id,
                'document': file_id,
                'caption': caption,
                'parse_mode': 'HTML'
            }).encode('utf-8')

            req = urllib.request.Request(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument",
                data=payload,
                headers={'Content-Type': 'application/x-www-form-urlencoded'}
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp_json = json.loads(resp.read().decode('utf-8'))
                if resp_json.get('ok'):
                    self.wfile.write(json.dumps({"ok": True, "name": target_mod['name']}).encode('utf-8'))
                else:
                    err_msg = resp_json.get('description', 'Telegram API error')
                    self.wfile.write(json.dumps({"ok": False, "error": err_msg}).encode('utf-8'))
        except Exception as e:
            self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode('utf-8'))
