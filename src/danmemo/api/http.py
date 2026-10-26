"""Native HTTP response base, without browser account endpoints."""

from http.server import BaseHTTPRequestHandler
import json


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, store, origin, secure_cookie=False, **kwargs):
        self.store, self.origin = (store, origin)
        super().__init__(*args, **kwargs)

    def log_message(self, *args):
        pass

    def respond(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.respond({"error": "Not found"}, 404)

    def do_POST(self):
        self.respond({"error": "Not found"}, 404)
