"""
api/index.py — RETIRED.

The API now runs on the VPS (bot/main.py behind Nginx). This Vercel function
used to hold a hardcoded bot token and let anyone trigger mod delivery
to any user_id, so it now only answers 410 Gone.
Safe to delete this file entirely once the VPS is live.
"""
from http.server import BaseHTTPRequestHandler
import json


class handler(BaseHTTPRequestHandler):
    def _gone(self):
        self.send_response(410)
        self.send_header("Content-type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": False, "error": "moved"}).encode())

    do_GET = do_POST = do_OPTIONS = _gone
