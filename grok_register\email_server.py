"""
Email receive API for EMAIL_MODE=custom
Cloudflare Email Routing -> Worker -> POST /webhook -> register polls GET /check/<email>

Usage:
  .\.venv\Scripts\python.exe -m grok_register.email_server
  # or
  .\.venv\Scripts\python.exe grok_register\email_server.py --domain example.com --port 8088
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from urllib.parse import unquote, urlparse

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DEFAULT_DOMAIN = (os.environ.get("EMAIL_DOMAIN") or "").strip()
DEFAULT_PORT = int(os.environ.get("EMAIL_PORT") or os.environ.get("PORT") or "8088")
WEBHOOK_TOKEN = (os.environ.get("WEBHOOK_TOKEN") or os.environ.get("EMAIL_WEBHOOK_TOKEN") or "").strip()

emails: dict[str, list[dict]] = {}
emails_lock = Lock()
CODE_TTL_SEC = int(os.environ.get("EMAIL_CODE_TTL_SEC") or "600")


def cleanup_old() -> None:
    now = time.time()
    with emails_lock:
        for addr in list(emails.keys()):
            emails[addr] = [e for e in emails[addr] if now - e["time"] < CODE_TTL_SEC]
            if not emails[addr]:
                del emails[addr]


def extract_code(text: str) -> str | None:
    if not text:
        return None
    m = re.search(r">([A-Z0-9]{3}-[A-Z0-9]{3})<", text)
    if m:
        return m.group(1).replace("-", "")
    m = re.search(r">([A-Z0-9]{6})<", text)
    if m:
        return m.group(1)
    m = re.search(r"\b([A-Z0-9]{3}-?[A-Z0-9]{3})\b", text)
    if m:
        return m.group(1).replace("-", "")
    m = re.search(r"(?i)(?:code|验证码|confirmation)\D{0,20}([A-Z0-9]{6})", text)
    if m:
        return m.group(1).upper()
    return None


def normalize_addr(value) -> list[str]:
    """Return lowercased bare emails from string/list CF payload."""
    out: list[str] = []
    if value is None:
        return out
    if isinstance(value, list):
        for item in value:
            out.extend(normalize_addr(item))
        return out
    s = str(value).strip()
    if not s:
        return out
    # "Name <a@b.com>" or plain
    found = re.findall(r"[\w.+\-]+@[\w.\-]+\.\w+", s)
    if found:
        out.extend(x.lower() for x in found)
    else:
        out.append(s.lower())
    return out


class EmailHandler(BaseHTTPRequestHandler):
    def _auth_ok(self) -> bool:
        if not WEBHOOK_TOKEN:
            return True
        got = self.headers.get("x-webhook-token") or self.headers.get("X-Webhook-Token") or ""
        q = urlparse(self.path).query
        if f"token={WEBHOOK_TOKEN}" in q:
            return True
        return got == WEBHOOK_TOKEN

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/webhook":
            self.send_response(404)
            self.end_headers()
            return
        if not self._auth_ok():
            self._json({"ok": False, "error": "unauthorized"}, status=401)
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8", "replace")
        try:
            data = json.loads(body) if body else {}
        except Exception:
            data = {}

        to_list = normalize_addr(data.get("to") or data.get("recipient") or "")
        from_addr = (normalize_addr(data.get("from") or data.get("sender") or "") or [""])[0]
        subject = data.get("subject") or ""
        text = data.get("text") or ""
        html = data.get("html") or ""
        content = f"{subject}\n{text}\n{html}"
        code = extract_code(content)

        stored = 0
        if code and to_list:
            with emails_lock:
                for to_addr in to_list:
                    emails.setdefault(to_addr, []).append(
                        {
                            "code": code,
                            "time": time.time(),
                            "from": from_addr,
                            "subject": subject,
                        }
                    )
                    stored += 1
                    print(f"[+] {to_addr} code={code}", flush=True)
        else:
            print(
                f"[?] webhook no code/to subject={subject!r} to={to_list} code={code}",
                flush=True,
            )

        self._json({"ok": True, "code": code, "stored": stored, "to": to_list})

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/health":
            self._json(
                {
                    "status": "ok",
                    "emails": len(emails),
                    "domain": DEFAULT_DOMAIN,
                    "auth": bool(WEBHOOK_TOKEN),
                }
            )
            return

        if path == "/domains":
            self._json({"domains": [DEFAULT_DOMAIN] if DEFAULT_DOMAIN else []})
            return

        if path.startswith("/check/"):
            cleanup_old()
            addr = unquote(path[len("/check/") :]).lower().strip()
            with emails_lock:
                items = emails.get(addr, [])
                if items:
                    latest = items[-1]
                    self._json({"code": latest["code"], "from": latest.get("from"), "subject": latest.get("subject")})
                else:
                    self._json({"code": None})
            return

        if path == "/list":
            cleanup_old()
            with emails_lock:
                result = {addr: len(msgs) for addr, msgs in emails.items()}
            self._json(result)
            return

        if path == "/":
            self._json(
                {
                    "service": "grok-custom-email",
                    "domain": DEFAULT_DOMAIN,
                    "endpoints": ["/health", "/domains", "/webhook", "/check/<email>", "/list"],
                }
            )
            return

        self.send_response(404)
        self.end_headers()

    def _json(self, data, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        print(f"[HTTP] {args[0] if args else format}", flush=True)


def main(argv=None):
    global DEFAULT_DOMAIN, DEFAULT_PORT
    argv = list(sys.argv[1:] if argv is None else argv)
    port = DEFAULT_PORT
    domain = DEFAULT_DOMAIN
    i = 0
    while i < len(argv):
        if argv[i] == "--port" and i + 1 < len(argv):
            port = int(argv[i + 1]); i += 2; continue
        if argv[i] == "--domain" and i + 1 < len(argv):
            domain = argv[i + 1]; i += 2; continue
        i += 1
    DEFAULT_DOMAIN = domain
    DEFAULT_PORT = port

    print(f"[*] Email server starting on 0.0.0.0:{port}", flush=True)
    print(f"[*] Domain : {domain or '(not set)'}", flush=True)
    print(f"[*] Webhook: http://0.0.0.0:{port}/webhook", flush=True)
    print(f"[*] Check  : http://127.0.0.1:{port}/check/<email>", flush=True)
    print(f"[*] Token  : {'enabled' if WEBHOOK_TOKEN else 'disabled'}", flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", port), EmailHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
