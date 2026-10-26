"""Read-only game content service; no administration or account website."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote
from danmemo.game import engine
from danmemo.content import web_assets


class ContentHandler(BaseHTTPRequestHandler):
    server_version = "DanMemoRe"

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.serve(False)

    def do_HEAD(self):
        self.serve(True)

    def serve(self, head):
        path = urlsplit(self.path).path
        if path in ("/health", "/storage/eu/status.json"):
            body = b'{"status":"ok"}' if path == "/health" else b'{"code":0}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head:
                self.wfile.write(body)
            return
        source = engine.content_path(self.path)
        if source is None or not source.is_file():
            self.send_error(404)
            return
        original = web_assets.resolve(unquote(path))
        if original is not None and source.suffix.lower() in (".html", ".htm"):
            body = web_assets.localize_html(
                source.read_text("utf-8"), engine.ENDPOINT
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head:
                self.wfile.write(body)
            return
        size = source.stat().st_size
        try:
            start, end, partial = engine.byte_range(self.headers.get("Range"), size)
        except ValueError:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return
        self.send_response(206 if partial else 200)
        self.send_header(
            "Content-Type",
            {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".css": "text/css",
                ".js": "text/javascript",
            }.get(source.suffix.lower(), "application/octet-stream"),
        )
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if not head:
            with source.open("rb") as stream:
                stream.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)


class ContentServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128
