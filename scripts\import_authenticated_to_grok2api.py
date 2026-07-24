#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Import auth-local/authenticated/xai-*.json into local grok2api (Build provider).

Mirrors the phone pack importer + local grok-import/import_accounts.py:
  1) POST /api/admin/v1/auth/login
  2) POST /api/admin/v1/accounts/import  (multipart files=)
  3) parse SSE data lines for created/updated stats

Credentials resolution order:
  --user/--password
  env GROK2API_ADMIN_USER / GROK2API_ADMIN_PASS
  <project>/keys/.credentials  (ADMIN_USER= / ADMIN_PASS=)
  <project>/.credentials
  sibling ../grok-import/.credentials
  ../grok2api/config.yaml bootstrapAdmin
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUTH_DIR = ROOT / "auth-local" / "authenticated"
DEFAULT_STATE = ROOT / "keys" / "g2a-imported-subs.txt"
DEFAULT_BATCH = 50
DEFAULT_ADMIN = "http://127.0.0.1:8000/api/admin/v1"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _strip_quotes(v: str) -> str:
    return v.strip().strip('"').strip("'")


def load_kv_credentials(path: Path) -> tuple[str | None, str | None]:
    if not path.exists():
        return None, None
    user = pw = None
    for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        v = _strip_quotes(v)
        if k in ("ADMIN_USER", "username", "USER"):
            user = v
        elif k in ("ADMIN_PASS", "password", "PASS", "PASSWORD"):
            pw = v
    return user, pw


def load_yaml_bootstrap(path: Path) -> tuple[str | None, str | None]:
    if not path.exists():
        return None, None
    user = pw = None
    in_block = False
    for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if ln.strip().startswith("bootstrapAdmin"):
            in_block = True
            continue
        if in_block:
            if ln and not ln.startswith(" ") and not ln.startswith("\t") and not ln.startswith("#"):
                break
            if "username:" in ln:
                user = _strip_quotes(ln.split(":", 1)[1])
            elif "password:" in ln:
                pw = _strip_quotes(ln.split(":", 1)[1])
    return user, pw


def resolve_credentials(user: str | None, password: str | None, cred_file: str | None) -> tuple[str, str]:
    if user and password:
        return user, password
    env_u = os.environ.get("GROK2API_ADMIN_USER") or os.environ.get("ADMIN_USER")
    env_p = os.environ.get("GROK2API_ADMIN_PASS") or os.environ.get("ADMIN_PASS")
    if env_u and env_p:
        return env_u, env_p
    candidates: list[Path] = []
    if cred_file:
        candidates.append(Path(cred_file))
    candidates.extend(
        [
            ROOT / "keys" / ".credentials",
            ROOT / ".credentials",
            ROOT.parent / "grok-import" / ".credentials",
            Path(r"./grok-import\.credentials"),
        ]
    )
    for p in candidates:
        u, pw = load_kv_credentials(p)
        if u and pw:
            log(f"creds from {p}")
            return u, pw
    u, pw = load_yaml_bootstrap(ROOT.parent / "grok2api" / "config.yaml")
    if u and pw:
        log("creds from grok2api/config.yaml bootstrapAdmin")
        return u, pw
    raise SystemExit(
        "missing admin credentials; set --user/--password or keys/.credentials "
        "(ADMIN_USER= / ADMIN_PASS=) or GROK2API_ADMIN_USER/PASS"
    )


def http_json(method: str, url: str, *, data: bytes | None = None, headers: dict | None = None, timeout: float = 60.0) -> tuple[int, str]:
    req = Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except HTTPError as e:
        body = e.read().decode("utf-8", "replace") if e.fp else ""
        return e.code, body
    except URLError as e:
        raise SystemExit(f"request failed: {url} -> {e}") from e


def login(base: str, user: str, password: str) -> str:
    code, body = http_json(
        "POST",
        base.rstrip("/") + "/auth/login",
        data=json.dumps({"username": user, "password": password}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    if code != 200:
        if code == 429 or "loginRateLimited" in body:
            raise SystemExit(f"login failed status=429 loginRateLimited body={body[:300]}")
        raise SystemExit(f"login failed status={code} body={body[:300]}")
    doc = json.loads(body)
    token = (
        doc.get("data", {}).get("tokens", {}).get("accessToken")
        or doc.get("data", {}).get("accessToken")
        or doc.get("accessToken")
    )
    if not token:
        raise SystemExit(f"login ok but no accessToken: {body[:300]}")
    return token


def account_key(raw: bytes) -> str:
    try:
        d = json.loads(raw)
        sub = d.get("sub")
        if sub:
            return f"sub:{sub}"
        email = d.get("email")
        if email:
            return f"email:{email}"
    except Exception:
        pass
    return "sha1:" + hashlib.sha1(raw).hexdigest()


def build_multipart(items: list[tuple[str, bytes]]) -> tuple[str, bytes]:
    boundary = "----g2a" + uuid.uuid4().hex
    out: list[bytes] = []
    b = boundary.encode()
    for name, content in items:
        out.append(b"--" + b + b"\r\n")
        out.append(
            f'Content-Disposition: form-data; name="files"; filename="{name}"\r\n'.encode()
        )
        out.append(b"Content-Type: application/json\r\n\r\n")
        out.append(content)
        out.append(b"\r\n")
    out.append(b"--" + b + b"--\r\n")
    return boundary, b"".join(out)


def parse_import_stats(text: str) -> dict[str, Any] | None:
    stats = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:") and ("created" in line or "updated" in line or "failed" in line):
            try:
                stats = json.loads(line[5:].strip())
            except Exception:
                pass
    return stats


def import_batch(base: str, token: str, items: list[tuple[str, bytes]], timeout: float) -> tuple[bool, dict | None, int, str]:
    boundary, body = build_multipart(items)
    code, text = http_json(
        "POST",
        base.rstrip("/") + "/accounts/import",
        data=body,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        timeout=timeout,
    )
    complete = "event: complete" in text or code == 200
    stats = parse_import_stats(text)
    return complete, stats, code, text[:500]


def load_state(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {ln.strip() for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip()}


def append_state(path: Path, keys: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for k in keys:
            f.write(k + "\n")


def collect_files(auth_dirs: list[Path], since_mtime: float | None, limit: int | None) -> list[Path]:
    files: list[Path] = []
    for d in auth_dirs:
        if not d.exists():
            continue
        for fp in sorted(d.glob("xai-*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            if since_mtime is not None and fp.stat().st_mtime < since_mtime:
                continue
            files.append(fp)
    # unique by path
    seen: set[str] = set()
    out: list[Path] = []
    for fp in files:
        key = str(fp.resolve())
        if key in seen:
            continue
        seen.add(key)
        out.append(fp)
    if limit and limit > 0:
        out = out[:limit]
    return out


def maybe_reload_token_manager(url: str) -> None:
    if not url:
        return
    try:
        code, body = http_json("POST", url, data=b"{}", headers={"Content-Type": "application/json"}, timeout=5)
        log(f"token_manager reload status={code} body={body[:120]}")
    except SystemExit as e:
        log(f"token_manager reload skip: {e}")
    except Exception as e:
        log(f"token_manager reload skip: {e}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Import authenticated xai-*.json into grok2api")
    p.add_argument("--auth-dir", action="append", default=[], help="dir with xai-*.json (repeatable)")
    p.add_argument("--admin-base", default=os.environ.get("GROK2API_ADMIN") or DEFAULT_ADMIN)
    p.add_argument("--user", default="")
    p.add_argument("--password", default="")
    p.add_argument("--credentials", default="", help="path to ADMIN_USER/ADMIN_PASS file")
    p.add_argument("--state-file", default=str(DEFAULT_STATE))
    p.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    p.add_argument("--limit", type=int, default=0, help="max files this run (0=all new)")
    p.add_argument("--since-minutes", type=float, default=0, help="only files newer than N minutes")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true", help="ignore state dedupe")
    p.add_argument("--timeout", type=float, default=600.0)
    p.add_argument("--batch-pause", type=float, default=1.5, help="seconds between batches to avoid admin rate limit")
    p.add_argument("--max-retries", type=int, default=8, help="retries per batch on 429/5xx")
    p.add_argument("--rate-limit-wait", type=float, default=90.0, help="seconds to wait on 429 loginRateLimited")
    p.add_argument("--reload-url", default=os.environ.get("TOKEN_MANAGER_RELOAD") or "http://127.0.0.1:8787/admin/reload")
    p.add_argument("--no-reload", action="store_true")
    p.add_argument("--health-url", default=os.environ.get("GROK2API_HEALTH") or "http://127.0.0.1:8000/healthz")
    args = p.parse_args(argv)

    auth_dirs = [Path(x) for x in args.auth_dir] if args.auth_dir else [DEFAULT_AUTH_DIR]
    since_mtime = None
    if args.since_minutes and args.since_minutes > 0:
        since_mtime = time.time() - args.since_minutes * 60

    # health
    try:
        code, body = http_json("GET", args.health_url, timeout=5)
        if code != 200:
            log(f"WARN grok2api health status={code} body={body[:100]}")
        else:
            log(f"grok2api health ok ({args.health_url})")
    except SystemExit as e:
        log(f"WARN grok2api health: {e}")

    files = collect_files(auth_dirs, since_mtime, args.limit if args.limit > 0 else None)
    if not files:
        log(f"no xai-*.json under {[str(d) for d in auth_dirs]}")
        return 0

    state_path = Path(args.state_file)
    processed = set() if args.force else load_state(state_path)

    items: list[tuple[str, bytes]] = []
    keys: list[str] = []
    for fp in files:
        try:
            raw = fp.read_bytes()
        except OSError:
            continue
        if not raw.strip():
            continue
        key = account_key(raw)
        if key in processed:
            continue
        # validate minimal schema
        try:
            doc = json.loads(raw)
        except Exception:
            log(f"skip invalid json {fp.name}")
            continue
        if not (doc.get("access_token") and doc.get("refresh_token")):
            log(f"skip incomplete {fp.name}")
            continue
        items.append((fp.name, raw))
        keys.append(key)

    log(f"scanned={len(files)} new={len(items)} dirs={[str(d) for d in auth_dirs]}")
    if not items:
        log("nothing new to import")
        return 0
    if args.dry_run:
        log(f"dry-run would import {len(items)} accounts; sample={items[0][0]}")
        return 0

    user, password = resolve_credentials(
        args.user or None,
        args.password or None,
        args.credentials or None,
    )
    done = 0
    batch = max(1, int(args.batch))
    last_stats = None
    token = login(args.admin_base, user, password)
    log("login ok")
    batch_pause = max(0.0, float(args.batch_pause))
    max_retries = max(1, int(args.max_retries))

    for i in range(0, len(items), batch):
        chunk = items[i : i + batch]
        chunk_keys = keys[i : i + batch]
        attempt = 0
        while True:
            attempt += 1
            complete, stats, code, preview = import_batch(args.admin_base, token, chunk, args.timeout)
            # token expired / unauthorized -> refresh once
            if code in (401, 403):
                log(f"token expired status={code}; re-login")
                try:
                    token = login(args.admin_base, user, password)
                except SystemExit as e:
                    if "429" in str(e) or "loginRateLimited" in str(e):
                        wait = float(args.rate_limit_wait)
                        log(f"login rate-limited; sleep {wait:.0f}s then retry ({attempt}/{max_retries})")
                        time.sleep(wait)
                        if attempt < max_retries:
                            continue
                    log(f"re-login failed: {e}")
                    return 1
                if attempt < max_retries:
                    continue
            if code == 429 or (isinstance(preview, str) and "loginRateLimited" in preview):
                wait = float(args.rate_limit_wait)
                log(f"rate limited status={code}; sleep {wait:.0f}s then poll again ({attempt}/{max_retries})")
                time.sleep(wait)
                # also refresh token after cooldown
                try:
                    token = login(args.admin_base, user, password)
                except SystemExit as e:
                    log(f"login after cooldown: {e}")
                if attempt < max_retries:
                    continue
                log(f"batch FAIL after retries preview={preview}")
                return 1
            if not complete and code >= 400:
                log(f"batch FAIL status={code} preview={preview}")
                if attempt < max_retries:
                    time.sleep(min(30, 5 * attempt))
                    continue
                return 1
            break

        last_stats = stats
        append_state(state_path, chunk_keys)
        done += len(chunk)
        log(f"batch ok size={len(chunk)} progress={done}/{len(items)} stats={json.dumps(stats) if stats else None}")
        if batch_pause > 0 and i + batch < len(items):
            time.sleep(batch_pause)

    if not args.no_reload:
        maybe_reload_token_manager(args.reload_url)

    summary = {
        "imported": done,
        "total_new": len(items),
        "last_stats": last_stats,
        "state_file": str(state_path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
