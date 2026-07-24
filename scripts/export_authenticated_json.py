#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Export OAuth credentials into auth-local/authenticated/xai-*.json

Canonical schema matches LocalAuthFileSink / CPA document:
{
  "type": "xai",
  "access_token": ...,
  "refresh_token": ...,
  "id_token": ...,
  "token_type": "Bearer",
  "expires_in": 21600,
  "expired": ISO-Z,
  "last_refresh": ISO-Z,
  "sub": subject,
  "base_url": "https://cli-chat-proxy.grok.com/v1",
  "token_endpoint": "https://auth.x.ai/oauth2/token",
  "auth_kind": "oauth"
}
Filename: xai-<hmac16>.json
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUTH_DIR = ROOT / "auth-local" / "authenticated"
DEFAULT_SALT_FILE = ROOT / "auth-local" / ".ledger-salt"
BASE_URL = "https://cli-chat-proxy.grok.com/v1"
TOKEN_ENDPOINT = "https://auth.x.ai/oauth2/token"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_salt(path: Path) -> bytes:
    if path.exists():
        raw = path.read_text(encoding="utf-8").strip()
        if raw:
            # stored as hex/text secret
            return raw.encode("utf-8")
    # fallback stable local salt
    return b"grok-free-register-local-salt"


def subject_from_jwt(token: str | None) -> str | None:
    if not token or not isinstance(token, str):
        return None
    parts = token.split(".")
    if len(parts) < 2:
        return None
    import base64
    pad = "=" * (-len(parts[1]) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + pad).decode("utf-8"))
    except Exception:
        return None
    for k in ("sub", "principal_id"):
        v = payload.get(k)
        if isinstance(v, str) and v:
            return v
    return None


def credential_filename(subject_or_refresh: str, salt: bytes) -> str:
    digest = hmac.new(salt, subject_or_refresh.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
    return f"xai-{digest}.json"


def to_document(
    *,
    access_token: str,
    refresh_token: str,
    id_token: str | None = None,
    token_type: str | None = "Bearer",
    expires_in: int | None = None,
    expires_at: str | None = None,
    last_refresh: str | None = None,
    subject: str | None = None,
    token_endpoint: str | None = None,
    email: str | None = None,
) -> dict:
    sub = subject or subject_from_jwt(id_token) or subject_from_jwt(access_token)
    now = now_iso()
    if not expires_at:
        # default 6h
        exp_in = int(expires_in or 21600)
        exp_ts = datetime.now(timezone.utc).timestamp() + exp_in
        expires_at = datetime.fromtimestamp(exp_ts, timezone.utc).isoformat().replace("+00:00", "Z")
        expires_in = exp_in
    else:
        expires_in = int(expires_in or 21600)
    doc = {
        "type": "xai",
        "access_token": access_token,
        "refresh_token": refresh_token,
        "id_token": id_token,
        "token_type": token_type or "Bearer",
        "expires_in": expires_in,
        "expired": expires_at,
        "last_refresh": last_refresh or now,
        "sub": sub,
        "base_url": BASE_URL,
        "token_endpoint": token_endpoint or TOKEN_ENDPOINT,
        "auth_kind": "oauth",
    }
    if email:
        doc["email"] = email
    return doc


def atomic_write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def store_document(doc: dict, auth_dir: Path, salt: bytes) -> Path:
    subject = doc.get("sub") or doc.get("refresh_token") or "unknown"
    filename = credential_filename(str(subject), salt)
    path = auth_dir / filename
    atomic_write_json(path, doc)
    return path


def from_oauth_line(obj: dict) -> dict:
    token = obj.get("token") if isinstance(obj.get("token"), dict) else obj
    return to_document(
        access_token=token.get("access_token") or obj.get("access_token") or "",
        refresh_token=token.get("refresh_token") or obj.get("refresh_token") or "",
        id_token=token.get("id_token") or obj.get("id_token"),
        token_type=token.get("token_type") or obj.get("token_type") or "Bearer",
        expires_in=token.get("expires_in") or obj.get("expires_in"),
        expires_at=token.get("expires_at") or token.get("expired") or obj.get("expired") or obj.get("expires_at"),
        last_refresh=token.get("last_refresh") or obj.get("last_refresh"),
        subject=token.get("subject") or token.get("sub") or obj.get("sub") or obj.get("subject"),
        token_endpoint=token.get("token_endpoint") or obj.get("token_endpoint"),
        email=obj.get("email") or token.get("email"),
    )


def main():
    import argparse
    p = argparse.ArgumentParser(description="Export oauth credentials into auth-local/authenticated")
    p.add_argument("--from-json", default="", help="single result json (pathb output)")
    p.add_argument("--from-jsonl", default="", help="oauth_credentials.jsonl")
    p.add_argument("--auth-dir", default=str(DEFAULT_AUTH_DIR))
    p.add_argument("--salt-file", default=str(DEFAULT_SALT_FILE))
    args = p.parse_args()

    auth_dir = Path(args.auth_dir)
    salt = load_salt(Path(args.salt_file))
    written = []

    if args.from_json:
        obj = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        if not obj.get("ok", True) and not (obj.get("token") or obj.get("access_token")):
            raise SystemExit(f"input not successful: {args.from_json}")
        doc = from_oauth_line(obj)
        if not doc["access_token"] or not doc["refresh_token"]:
            raise SystemExit("missing access/refresh token")
        path = store_document(doc, auth_dir, salt)
        written.append(str(path))

    if args.from_jsonl:
        for line in Path(args.from_jsonl).read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s:
                continue
            obj = json.loads(s)
            doc = from_oauth_line(obj)
            if not doc["access_token"] or not doc["refresh_token"]:
                continue
            path = store_document(doc, auth_dir, salt)
            written.append(str(path))

    if not written:
        raise SystemExit("nothing written; pass --from-json or --from-jsonl")
    print(json.dumps({"written": written, "count": len(written)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
