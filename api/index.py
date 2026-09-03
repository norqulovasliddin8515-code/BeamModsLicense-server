from http.server import BaseHTTPRequestHandler
import json
import os
import urllib.request
import urllib.parse

BOT_TOKEN = os.getenv("BOT_TOKEN", "8665911741:AAHg-R9XtJjDVaGxX6H5eG7AB8koA95YH3g")

VERCEL_URL = "https://beam-mods-license-server.vercel.app"


def _cors_headers(handler_obj):
    """Umumiy CORS headerlarni yuborish."""
    handler_obj.send_header('Access-Control-Allow-Origin', '*')
    handler_obj.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
    handler_obj.send_header('Access-Control-Allow-Headers', 'Content-Type, ngrok-skip-browser-warning')


def _load_mods_json():
    """mods.json faylini o'qish va modlar ro'yxatini qaytarish."""
    mods_file = os.path.join(os.path.dirname(__file__), '..', 'mods.json')
    if not os.path.exists(mods_file):
        mods_file = os.path.join(os.getcwd(), 'mods.json')
    if os.path.exists(mods_file):
        with open(mods_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"ok": True, "count": 0, "mods": []}


def _resolve_image_url(image_url: str) -> str:
    """
    tg_photo: prefiksi bilan saqlangan rasmlarni Vercel proxy URL ga aylantirish.
    Telegram file_id → /api/photo/{file_id}
    """
    if not image_url:
        return ''
    if image_url.startswith('tg_photo:'):
        photo_id = image_url.replace('tg_photo:', '')
        return f"{VERCEL_URL}/api/photo/{photo_id}"
    return image_url


class handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(204)
        _cors_headers(self)
        self.end_headers()

    def do_GET(self):
        path = self.path.split('?')[0]  # Query stringni olib tashlash

        # ── /api/photo/{file_id} — Telegram rasmini CDN-ga redirect ──
        if path.startswith('/api/photo/'):
            file_id = path.replace('/api/photo/', '').strip('/')
            if not file_id:
                self.send_response(400)
                _cors_headers(self)
                self.end_headers()
                self.wfile.write(b"file_id kerak")
                return

            try:
                api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={file_id}"
                req = urllib.request.Request(api_url)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode('utf-8'))

                if data.get('ok') and data.get('result', {}).get('file_path'):
                    file_path = data['result']['file_path']
                    cdn_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
                    # 302 redirect
                    self.send_response(302)
                    _cors_headers(self)
                    self.send_header('Location', cdn_url)
                    self.send_header('Cache-Control', 'public, max-age=3600')
                    self.end_headers()
                else:
                    self.send_response(404)
                    _cors_headers(self)
                    self.end_headers()
                    self.wfile.write(b"Photo not found")
            except Exception as e:
                self.send_response(500)
                _cors_headers(self)
                self.end_headers()
                self.wfile.write(str(e).encode('utf-8'))
            return

        # ── /api/mods yoki /api/download (GET) — mods.json qaytarish ──
        self.send_response(200)
        self.send_header('Content-type', 'application/json; charset=utf-8')
        _cors_headers(self)
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.end_headers()

        try:
            data = _load_mods_json()
            # tg_photo: URL larni resolve qilish
            for mod in data.get('mods', []):
                mod['image_url'] = _resolve_image_url(mod.get('image_url', ''))
                mod['thumbnail'] = mod['image_url']  # thumbnail = image_url bilan bir xil
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))
        except Exception as e:
            self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode('utf-8'))

    def do_POST(self):
        path = self.path.split('?')[0]

        # CORS headers
        self.send_response(200)
        self.send_header('Content-type', 'application/json; charset=utf-8')
        _cors_headers(self)
        self.end_headers()

        length = int(self.headers.get('content-length', 0))
        body = {}
        if length > 0:
            try:
                body = json.loads(self.rfile.read(length).decode('utf-8'))
            except Exception:
                pass

        # ── /api/download — Faylni Telegram DM ga yuborish ──
        if '/download' in path:
            mod_id  = body.get('mod_id')
            user_id = body.get('user_id')

            if not mod_id or not user_id:
                self.wfile.write(json.dumps({"ok": False, "error": "mod_id va user_id kerak"}).encode('utf-8'))
                return

            try:
                mod_id  = int(mod_id)
                user_id = int(user_id)
            except (ValueError, TypeError):
                self.wfile.write(json.dumps({"ok": False, "error": "Noto'g'ri parametrlar"}).encode('utf-8'))
                return

            # mods.json dan modni topish
            target_mod = None
            try:
                data = _load_mods_json()
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
                self.wfile.write(json.dumps({
                    "ok": False, "error": "demo", "name": target_mod.get('name')
                }).encode('utf-8'))
                return

            # Telegram Bot API orqali faylni yuborish (narxsiz caption)
            try:
                desc = target_mod.get('description', '') or ''
                caption = (
                    f"<b>{target_mod['name']}</b>\n"
                    f"Kategoriya: {target_mod.get('category', '')}\n\n"
                    f"{desc}\n\n"
                    f"BeamModsStudio orqali yuklandi ✅"
                )

                payload = urllib.parse.urlencode({
                    'chat_id':    user_id,
                    'document':   file_id,
                    'caption':    caption,
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
            return

        # Boshqa POST so'rovlar
        self.wfile.write(json.dumps({"ok": False, "error": "Not found"}).encode('utf-8'))
