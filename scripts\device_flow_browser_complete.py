#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Path B: closed-loop Device OAuth via accounts.x.ai browser approval.

Flow:
  1) httpx: discovery + start device_code on auth.x.ai
  2) browser: open verification_url, inject cookies/SSO, click Allow
  3) httpx: poll token endpoint until access/refresh token

This is the practical path after xAI moved approval UI to accounts.x.ai.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

from xai_enroller.protocol import XAIProfile, XAIProtocol
from xai_enroller.service import build_http_client

# reuse path A helpers
from scripts.browser_device_authorize import (  # type: ignore
    authorize as browser_authorize,
    build_verification_url,
    load_source_line,
)


async def run_once(
    *,
    source_file: Path,
    source_index: int,
    headed: bool,
    timeout_sec: float,
    poll_timeout: float,
    json_out: Path | None,
) -> dict:
    sess = load_source_line(source_file, source_index)
    email = sess.get("email") or f"source#{source_index}"
    sso = sess.get("sso") or ""
    cookies = list(sess.get("cookies") or [])
    if not sso and not cookies:
        raise ValueError("source has no sso/cookies")

    client = build_http_client(timeout=max(60.0, poll_timeout))
    protocol = XAIProtocol(client, XAIProfile.default(), default_poll_interval=5.0)
    out = {
        "ok": False,
        "email": email,
        "stage": "start",
        "user_code": "",
        "verification_url": "",
        "browser": None,
        "token": None,
        "error": "",
    }
    try:
        flow = await protocol.start_device_flow()
        out["user_code"] = flow.user_code
        out["verification_url"] = flow.verification_url
        out["stage"] = "browser"
        print(f"[B] device started user_code={flow.user_code}", flush=True)
        print(f"[B] verification_url={flow.verification_url}", flush=True)

        # Prefer official verification_url from discovery response.
        browser_result = await browser_authorize(
            cookies=cookies,
            sso=sso or None,
            verification_url=flow.verification_url or build_verification_url(flow.user_code, None),
            user_code=flow.user_code,
            headed=headed,
            timeout_sec=timeout_sec,
            keep_open=False,
        )
        out["browser"] = browser_result
        if not browser_result.get("ok"):
            out["error"] = f"browser_{browser_result.get('reason') or 'failed'}"
            out["stage"] = "browser_failed"
            return out

        out["stage"] = "poll_token"
        print("[B] browser authorized; polling token...", flush=True)
        cred = await protocol.poll_token(
            endpoint=flow.token_endpoint,
            flow=flow,
            timeout=poll_timeout,
        )
        out["ok"] = True
        out["stage"] = "done"
        out["token"] = {
            "access_token": cred.access_token,
            "refresh_token": cred.refresh_token,
            "id_token": cred.id_token,
            "expires_at": cred.expires_at,
            "subject": cred.subject,
            "token_endpoint": cred.token_endpoint,
        }
        # append keys + canonical authenticated json
        keys = ROOT / "keys"
        keys.mkdir(exist_ok=True)
        line = {
            "email": email,
            "access_token": cred.access_token,
            "refresh_token": cred.refresh_token,
            "id_token": cred.id_token,
            "expires_at": cred.expires_at,
            "subject": cred.subject,
            "source": "device_flow_browser_complete",
        }
        with (keys / "oauth_credentials.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
        with (keys / "refresh_tokens.txt").open("a", encoding="utf-8") as f:
            f.write(f"{email}\t{cred.refresh_token}\n")
        try:
            from scripts.export_authenticated_json import from_oauth_line, store_document, load_salt
            auth_dir = ROOT / "auth-local" / "authenticated"
            salt = load_salt(ROOT / "auth-local" / ".ledger-salt")
            doc = from_oauth_line(line | {
                "token_type": getattr(cred, "token_type", None) or "Bearer",
                "expires_in": getattr(cred, "expires_in", None),
                "last_refresh": getattr(cred, "last_refresh", None),
                "token_endpoint": getattr(cred, "token_endpoint", None),
            })
            auth_path = store_document(doc, auth_dir, salt)
            out["authenticated_json"] = str(auth_path)
            print(f"[B] authenticated json -> {auth_path}", flush=True)
        except Exception as exp:
            print(f"[B] authenticated json export warn: {exp}", flush=True)
        print(f"[B] SUCCESS email={email} subject={cred.subject}", flush=True)
        return out
    except Exception as e:
        out["error"] = f"{type(e).__name__}:{e}"
        print(f"[B] FAIL {out['error']}", flush=True)
        return out
    finally:
        await client.aclose()
        if json_out:
            json_out.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_parser():
    p = argparse.ArgumentParser(description="Closed-loop device OAuth with accounts.x.ai browser approval")
    p.add_argument("--source-file", required=True, help="auth-local/source-snapshot.jsonl or id\\tsso file")
    p.add_argument("--source-index", type=int, default=0)
    p.add_argument("--headed", action="store_true")
    p.add_argument("--browser-timeout", type=float, default=120.0)
    p.add_argument("--poll-timeout", type=float, default=180.0)
    p.add_argument("--json-out", default="")
    p.add_argument("--count", type=int, default=1, help="how many sequential sources starting at index")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    source = Path(args.source_file)
    ok_n = 0
    fail_n = 0
    for i in range(max(1, args.count)):
        idx = args.source_index + i
        print(f"\n===== PATH B item index={idx} =====", flush=True)
        json_out = Path(args.json_out) if args.json_out and args.count == 1 else None
        if args.json_out and args.count > 1:
            json_out = Path(args.json_out).with_name(
                Path(args.json_out).stem + f"_{idx}" + Path(args.json_out).suffix
            )
        result = asyncio.run(
            run_once(
                source_file=source,
                source_index=idx,
                headed=bool(args.headed),
                timeout_sec=float(args.browser_timeout),
                poll_timeout=float(args.poll_timeout),
                json_out=json_out,
            )
        )
        if result.get("ok"):
            ok_n += 1
        else:
            fail_n += 1
            # stop early if first source invalid style errors
            if str(result.get("error", "")).startswith("browser_login_required"):
                break
    print(json.dumps({"ok": ok_n, "fail": fail_n}, ensure_ascii=False), flush=True)
    return 0 if ok_n > 0 and fail_n == 0 else (0 if ok_n > 0 else 1)


if __name__ == "__main__":
    raise SystemExit(main())
