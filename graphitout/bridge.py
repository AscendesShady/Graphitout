"""Authenticated loopback-only editor bridge, independent of the MCP transport."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .model import ConflictError, GraphStore, MAX_BYTES, implementation_brief

LOG = logging.getLogger(__name__)


class EditorBridge:
    def __init__(self, store: GraphStore, editor: Path, port: int = 0):
        self.store, self.editor = store, editor
        self.token = secrets.token_urlsafe(32)
        self.cookie = "graphitout_" + self.token[:12]
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass  # Requests can contain a session token; never log URLs.

            def reply(self, code: int, content, content_type="application/json", headers=None):
                if isinstance(content, (dict, list)):
                    content = json.dumps(content, ensure_ascii=False, allow_nan=False).encode("utf-8")
                elif isinstance(content, str):
                    content = content.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", content_type + "; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(content)

            def authorize(self, mutation=False):
                host = f"127.0.0.1:{bridge.server.server_port}"
                if self.headers.get("Host") != host:
                    self.reply(403, {"error": "Unexpected host."})
                    return False
                origin = self.headers.get("Origin")
                if origin and origin != bridge.origin or mutation and origin != bridge.origin:
                    self.reply(403, {"error": "Only the local editor origin is allowed."})
                    return False
                cookie = SimpleCookie()
                try:
                    cookie.load(self.headers.get("Cookie", ""))
                    value = cookie.get(bridge.cookie)
                    valid = value is not None and hmac.compare_digest(value.value, bridge.token)
                except (ValueError, TypeError):
                    valid = False
                if not valid:
                    self.reply(403, {"error": "Open the editor link returned by get_project_info."})
                return valid

            def do_GET(self):
                parsed = urlsplit(self.path)
                token = parse_qs(parsed.query).get("session", [""])[0]
                if parsed.path == "/" and token:
                    if self.headers.get("Host") != f"127.0.0.1:{bridge.server.server_port}" or not hmac.compare_digest(token.encode("utf-8"), bridge.token.encode("utf-8")):
                        self.reply(403, {"error": "Invalid session."})
                        return
                    self.reply(303, b"", headers={"Location": "/", "Set-Cookie": f"{bridge.cookie}={bridge.token}; Path=/; HttpOnly; SameSite=Strict"})
                    return
                if not self.authorize():
                    return
                try:
                    if parsed.path == "/":
                        state = bridge.store.read()
                        html = bridge.editor.read_text(encoding="utf-8")
                        embedded = json.dumps({"graph": state["graph"], "snap": True}, ensure_ascii=False).replace("<", "\\u003c")
                        config = json.dumps({"revision": state["revision"], "storageKey": "graphitout.project." + hashlib.sha256(str(bridge.store.root).encode()).hexdigest()[:20]})
                        html = re.sub(r'(<script id="initial-graph" type="application/json">)[\s\S]*?(</script>)', lambda m: m[1] + embedded + m[2], html, count=1)
                        html = re.sub(r'(<script id="bridge-config" type="application/json">)[\s\S]*?(</script>)', lambda m: m[1] + config + m[2], html, count=1)
                        self.reply(200, html, "text/html")
                    elif parsed.path == "/api/graph":
                        self.reply(200, bridge.store.read())
                    elif parsed.path == "/api/brief":
                        self.reply(200, {"markdown": implementation_brief(bridge.store.read()["graph"])})
                    else:
                        self.reply(404, {"error": "Not found."})
                except (ValueError, OSError, TypeError, json.JSONDecodeError):
                    LOG.exception("Editor read failed")
                    self.reply(500, {"error": "Could not read the saved graph. Check server diagnostics."})

            def do_POST(self):
                if not self.authorize(mutation=True):
                    return
                if urlsplit(self.path).path != "/api/graph":
                    self.reply(404, {"error": "Not found."})
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= MAX_BYTES:
                        raise ValueError("Graph body must be below 16 MB.")
                    body = json.loads(self.rfile.read(size))
                    state = bridge.store.save(body["graph"], body["expectedRevision"])
                    self.reply(200, state)
                except ConflictError as error:
                    self.reply(409, {"error": str(error), "revision": bridge.store.read()["revision"]})
                except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
                    self.reply(400, {"error": str(error)})

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        self.url = self.origin + "/?session=" + self.token
        self.thread = threading.Thread(target=self.server.serve_forever, name="graphitout-editor", daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
