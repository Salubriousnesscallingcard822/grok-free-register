"""
Cloud mailbox broker (tempmail.lol backend)
==========================================
Provides a local/Azure HTTP API that looks like a stable OTP mailbox service:

  POST /mailbox/create        -> {"email","handle","provider"}
  GET  /mailbox/<handle>      -> {"email","messages":[...],"code": "..."}
  GET  /health

This is the practical "cloud email OTP" path when Cloudflare Email Routing
API token / custom domain is unavailable. Register can still use EMAIL_MODE=tempmail
directly; this broker is for remote pool machines / Azure hosting.
"""
from __future__ import annotations

import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from urllib.parse import urlparse

import requests

PROXY = os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY") or ""
PROXIES = {"http": PROXY, "https": PROXY} if PROXY else None
PORT = int(os.environ.get("MAILBOX_BROKER_PORT") or os.environ.get("PORT") or "8090")
TOKEN = (os.environ.get("MAILBOX_BROKER_TOKEN") or "").strip()
TTL_SEC = int(os.environ.get("MAILBOX_TTL_SEC") or "900")

BOXES = {}
LOCK = Lock()


def extract_code(text: str):
    if not text:
        return None
    for pat in (r'>([A-Z0-9]{3}-[A-Z0-9]{3})<', r'>([A-Z0-9]{6})<', r'\b([A-Z0-9]{3}-?[A-Z0-9]{3})\b'):
        m = re.search(pat, text)
        if m:
            return m.group(1).replace('-', '')
    return None


def lol_create():
    r = requests.post('https://api.tempmail.lol/v2/inbox/create', timeout=20, proxies=PROXIES)
    r.raise_for_status()
    data = r.json()
    addr, tok = data.get('address'), data.get('token')
    if not addr or not tok:
        raise RuntimeError('tempmail.lol create failed')
    return addr, tok


def lol_fetch(tok: str):
    r = requests.get(f'https://api.tempmail.lol/v2/inbox?token={tok}', timeout=15, proxies=PROXIES)
    r.raise_for_status()
    data = r.json()
    return data.get('emails') or data.get('messages') or []


def cleanup():
    now = time.time()
    with LOCK:
        dead = [k for k, v in BOXES.items() if now - v['created'] > TTL_SEC]
        for k in dead:
            BOXES.pop(k, None)


class Handler(BaseHTTPRequestHandler):
    def _auth_ok(self):
        if not TOKEN:
            return True
        return self.headers.get('x-mailbox-token') == TOKEN or self.headers.get('Authorization') == f'Bearer {TOKEN}'

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('content-type', 'application/json; charset=utf-8')
        self.send_header('content-length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        cleanup()
        if not self._auth_ok():
            return self._json(401, {'error': 'unauthorized'})
        path = urlparse(self.path).path
        if path == '/health':
            return self._json(200, {'ok': True, 'provider': 'tempmail.lol', 'boxes': len(BOXES)})
        if path.startswith('/mailbox/'):
            handle = path.split('/', 2)[-1]
            with LOCK:
                box = BOXES.get(handle)
            if not box:
                return self._json(404, {'error': 'not found'})
            try:
                items = lol_fetch(box['token'])
            except Exception as exc:
                return self._json(502, {'error': f'fetch failed: {exc}'})
            text = '\n'.join(
                f"{i.get('subject','')}\n{i.get('body','')}\n{i.get('html','')}"
                for i in items if isinstance(i, dict)
            )
            code = extract_code(text)
            return self._json(200, {
                'email': box['email'],
                'provider': 'tempmail.lol',
                'messages': items,
                'code': code,
            })
        return self._json(404, {'error': 'not found'})

    def do_POST(self):
        cleanup()
        if not self._auth_ok():
            return self._json(401, {'error': 'unauthorized'})
        path = urlparse(self.path).path
        if path != '/mailbox/create':
            return self._json(404, {'error': 'not found'})
        try:
            email, tok = lol_create()
        except Exception as exc:
            return self._json(502, {'error': f'create failed: {exc}'})
        handle = f'lol|{tok}'
        with LOCK:
            BOXES[handle] = {'email': email, 'token': tok, 'created': time.time()}
        return self._json(201, {'email': email, 'handle': handle, 'provider': 'tempmail.lol'})

    def log_message(self, fmt, *args):
        print(f"[mailbox-broker] {self.address_string()} {fmt % args}")


def main():
    server = ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    print(f"[mailbox-broker] listening on 0.0.0.0:{PORT} provider=tempmail.lol proxy={PROXY or '-'}")
    server.serve_forever()


if __name__ == '__main__':
    main()
