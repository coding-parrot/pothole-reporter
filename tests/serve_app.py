#!/usr/bin/env python3
"""Serve the packaged app and its separately hosted state packs for browser tests."""

from __future__ import annotations

import argparse
import gzip
import mimetypes
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = (ROOT / "android-app" / "www").resolve()
WEB_ROOT = (ROOT / "static").resolve()
PACK_ROOT = (ROOT / "docs" / "packs" / "v1").resolve()
PACK_PREFIX = "/packs/v1/"
WEB_PREFIX = "/web-app/"


class AppHandler(SimpleHTTPRequestHandler):
    """Map Android, current-web and immutable-pack URLs to explicit roots."""

    def translate_path(self, path: str) -> str:
        request_path = unquote(urlsplit(path).path)
        if request_path.startswith(PACK_PREFIX):
            base = PACK_ROOT
            relative = request_path[len(PACK_PREFIX) :]
        elif request_path.startswith(WEB_PREFIX):
            base = WEB_ROOT
            relative = request_path[len(WEB_PREFIX) :] or "index.html"
        else:
            base = APP_ROOT
            relative = request_path.lstrip("/") or "index.html"

        candidate = (base / relative).resolve()
        try:
            candidate.relative_to(base)
        except ValueError:
            # A path outside either explicit root must never be served.
            return str(base / "__not_found__")
        return str(candidate)

    def end_headers(self) -> None:
        # Keep public JSON usable by an optional cross-origin harness too. Normal suite
        # requests are same-origin and therefore do not rely on a security bypass.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _serve_compressed_pack(self, *, head_only: bool) -> bool:
        request_path = unquote(urlsplit(self.path).path)
        if not request_path.startswith(PACK_PREFIX) or "gzip" not in self.headers.get(
            "Accept-Encoding", ""
        ).lower():
            return False
        path = Path(self.translate_path(self.path))
        if not path.is_file():
            self.send_error(404, "File not found")
            return True
        raw = path.read_bytes()
        encoded = gzip.compress(raw, compresslevel=6, mtime=0)
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Encoding", "gzip")
        # Deliberately the transfer size, as on GitHub Pages. Fetch exposes decoded
        # bytes, so production code must verify those bytes rather than this header.
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Last-Modified", self.date_time_string(path.stat().st_mtime))
        self.end_headers()
        if not head_only:
            self.wfile.write(encoded)
        return True

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if not self._serve_compressed_pack(head_only=False):
            super().do_GET()

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib handler API
        if not self._serve_compressed_pack(head_only=True):
            super().do_HEAD()

    def list_directory(self, path: str):  # type: ignore[override]
        self.send_error(404, "Directory listing disabled")
        return None

    def log_message(self, format: str, *args: object) -> None:
        # The suite already reports failed requests; keep its server log useful.
        if args and str(args[1]).startswith(("4", "5")):
            super().log_message(format, *args)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    mimetypes.add_type("application/json", ".json")
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer((args.host, args.port), AppHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
