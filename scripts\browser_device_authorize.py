#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Browser OAuth Device Authorization for accounts.x.ai (Path A).

xAI moved device approval to the accounts.x.ai web front-end.
Server-side auth.x.ai can only start device_code + poll tokens.
Approval must happen in a real browser session.

This script:
  1) launches Chromium (CloakBrowser if present)
  2) injects full cookie jar / SSO from source snapshot
  3) opens verification_url (or builds from user_code)
  4) fills user_code, dismisses banners, clicks Allow/Authorize
  5) waits until device authorized /oauth2/device/done

Examples:
  python scripts/browser_device_authorize.py ^
    --source-file auth-local/source-snapshot.jsonl ^
    --user-code ABCD-EFGH --headed

  python scripts/browser_device_authorize.py ^
    --sso eyJ... --verification-url "https://accounts.x.ai/..." --headed
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlencode

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass


def _env_proxy_url() -> str | None:
    for key in (
        "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy",
    ):
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    return None


def playwright_proxy_settings() -> dict | None:
    proxy = _env_proxy_url()
    if not proxy:
        return None
    raw = proxy.strip()
    if "://" not in raw:
        raw = "http://" + raw
    parsed = urlparse(raw)
    if not parsed.hostname:
        return {"server": raw}
    scheme = parsed.scheme or "http"
    port = f":{parsed.port}" if parsed.port else ""
    settings = {"server": f"{scheme}://{parsed.hostname}{port}"}
    if parsed.username:
        settings["username"] = parsed.username
    if parsed.password is not None:
        settings["password"] = parsed.password
    return settings


def find_chrome() -> str | None:
    for key in ("CHROME_PATH", "CLOAK_BROWSER_PATH"):
        p = os.environ.get(key)
        if p and Path(p).exists():
            return p
    try:
        import cloakbrowser
        try:
            cloakbrowser.ensure_binary()
        except Exception:
            pass
        info = cloakbrowser.binary_info() if hasattr(cloakbrowser, "binary_info") else {}
        bp = (info or {}).get("binary_path") if isinstance(info, dict) else None
        if bp and Path(bp).exists():
            return bp
        for cand in Path.home().joinpath(".cloakbrowser").rglob("chrome.exe"):
            return str(cand)
        for cand in Path.home().joinpath(".cloakbrowser").rglob("chrome"):
            return str(cand)
    except Exception:
        pass
    for cand in (ROOT / ".cloakbrowser").rglob("chrome.exe"):
        return str(cand)
    return None


def _normalize_cookie(raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    value = raw.get("value")
    if not name or value is None:
        return None
    cookie = {
        "name": str(name),
        "value": str(value),
        "path": str(raw.get("path") or "/"),
        "secure": bool(raw.get("secure", True)),
        "httpOnly": bool(raw.get("httpOnly", True)),
    }
    domain = raw.get("domain")
    if domain:
        cookie["domain"] = str(domain)
    else:
        cookie["domain"] = "accounts.x.ai"
    # Playwright sameSite values
    same = raw.get("sameSite")
    if isinstance(same, str) and same.lower() in {"strict", "lax", "none"}:
        cookie["sameSite"] = same.capitalize() if same.lower() != "none" else "None"
    expires = raw.get("expires")
    if isinstance(expires, (int, float)) and expires > 0:
        cookie["expires"] = float(expires)
    return cookie


def load_session_from_source_file(path: Path) -> dict:
    """Return {email,sso,cookies[]} from tab file / json / jsonl."""
    text = path.read_text(encoding="utf-8")
    # prefer jsonl first object with cookies
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("{"):
            try:
                obj = json.loads(s)
            except Exception:
                continue
            cookies = []
            raw_cookies = obj.get("cookies") or []
            if isinstance(raw_cookies, list):
                for c in raw_cookies:
                    nc = _normalize_cookie(c)
                    if nc:
                        cookies.append(nc)
            sso = ""
            for c in cookies:
                if c.get("name") == "sso" and c.get("value"):
                    sso = c["value"]
                    break
            if not sso:
                for c in cookies:
                    if str(c.get("name", "")).startswith("sso") and c.get("value"):
                        sso = c["value"]
                        break
            for k in ("sso", "sso_token", "token"):
                if not sso and isinstance(obj.get(k), str):
                    sso = obj[k]
            if not sso and not cookies:
                continue
            # ensure sso cookies for accounts/auth domains
            if sso:
                have = {(c.get("name"), c.get("domain")) for c in cookies}
                for domain in ("accounts.x.ai", ".x.ai", "auth.x.ai", ".auth.x.ai"):
                    key = ("sso", domain)
                    if key not in have:
                        cookies.append({
                            "name": "sso",
                            "value": sso,
                            "domain": domain,
                            "path": "/",
                            "secure": True,
                            "httpOnly": True,
                        })
            return {
                "email": obj.get("email") or "",
                "sso": sso,
                "cookies": cookies,
            }
        if "\t" in s:
            _id, token = s.split("\t", 1)
            token = token.strip()
            if token:
                return {
                    "email": _id,
                    "sso": token,
                    "cookies": [
                        {"name": "sso", "value": token, "domain": d, "path": "/", "secure": True, "httpOnly": True}
                        for d in ("accounts.x.ai", ".x.ai", "auth.x.ai", ".auth.x.ai")
                    ],
                }
        if len(s) > 20 and " " not in s:
            return {
                "email": "",
                "sso": s,
                "cookies": [
                    {"name": "sso", "value": s, "domain": d, "path": "/", "secure": True, "httpOnly": True}
                    for d in ("accounts.x.ai", ".x.ai", "auth.x.ai", ".auth.x.ai")
                ],
            }
    raise ValueError(f"no session/sso found in {path}")


def build_verification_url(user_code: str | None, verification_url: str | None) -> str:
    if verification_url:
        return verification_url
    if not user_code:
        raise ValueError("need --verification-url or --user-code")
    # Prefer complete verify entry; accounts front-end handles login+consent
    return "https://accounts.x.ai/sign-in?" + urlencode({
        "redirect": "device",
        "user_code": user_code,
    })


async def click_any(page, names: tuple[str, ...]) -> bool:
    for name in names:
        try:
            btn = page.get_by_role("button", name=name, exact=True)
            if await btn.count() and await btn.first.is_visible():
                await btn.first.click(timeout=2000)
                return True
        except Exception:
            pass
        try:
            loc = page.locator(
                f'button:has-text("{name}"), '
                f'[role="button"]:has-text("{name}"), '
                f'input[type="submit"][value="{name}"]'
            )
            if await loc.count() and await loc.first.is_visible():
                await loc.first.click(timeout=2000)
                return True
        except Exception:
            pass
    return False


async def authorize(
    *,
    cookies: list[dict],
    sso: str | None,
    verification_url: str,
    user_code: str | None,
    headed: bool,
    timeout_sec: float,
    keep_open: bool,
) -> dict:
    from playwright.async_api import async_playwright

    chrome = find_chrome()
    proxy = playwright_proxy_settings()
    result = {
        "ok": False,
        "reason": "",
        "final_url": "",
        "chrome": chrome,
        "proxy_server": (proxy or {}).get("server"),
        "verification_url": verification_url,
        "cookies_injected": len(cookies or []),
    }

    async with async_playwright() as pw:
        launch_opts = {"headless": not headed}
        if chrome:
            launch_opts["executable_path"] = chrome
        if proxy:
            launch_opts["proxy"] = proxy
        browser = await pw.chromium.launch(**launch_opts)
        context_opts = {}
        if proxy:
            context_opts["proxy"] = proxy
        context = await browser.new_context(**context_opts)
        if cookies:
            # Playwright requires url or domain; already set domain
            try:
                await context.add_cookies(cookies)
            except Exception as e:
                print(f"[!] add_cookies warn: {e}", flush=True)
                # fallback sso only
                if sso:
                    await context.add_cookies([
                        {"name": "sso", "value": sso, "domain": d, "path": "/", "secure": True, "httpOnly": True}
                        for d in ("accounts.x.ai", ".x.ai")
                    ])
        page = await context.new_page()

        # warm accounts domain then open verify url
        try:
            await page.goto("https://accounts.x.ai/", wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            print(f"[!] warm accounts.x.ai warn: {e}", flush=True)
        print(f"[*] goto {verification_url}", flush=True)
        await page.goto(verification_url, wait_until="domcontentloaded", timeout=60000)

        if user_code:
            try:
                code_input = page.locator(
                    'input[name="user_code"], input[autocomplete="one-time-code"], input[inputmode="text"]'
                )
                if await code_input.count() and await code_input.first.is_visible():
                    el = code_input.first
                    current = ""
                    try:
                        current = await el.input_value()
                    except Exception:
                        pass
                    if user_code.replace("-", "") not in (current or "").replace("-", ""):
                        await el.fill("")
                        await el.press_sequentially(user_code)
                    await click_any(page, ("Continue", "继续", "Next", "下一步", "Submit", "提交", "Verify", "验证"))
            except Exception as e:
                print(f"[!] fill user_code warn: {e}", flush=True)

        deadline = asyncio.get_running_loop().time() + timeout_sec
        consent_done = False
        while asyncio.get_running_loop().time() < deadline:
            url = page.url
            result["final_url"] = url
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            if parsed.scheme != "https" or not (host == "x.ai" or host.endswith(".x.ai")):
                result["reason"] = f"unsafe_page:{url}"
                break
            try:
                text = (await page.locator("body").inner_text(timeout=3000)).lower()
            except Exception:
                text = ""
            try:
                title = (await page.title()).lower()
            except Exception:
                title = ""

            if "/oauth2/device/done" in parsed.path or any(
                m in text
                for m in (
                    "device authorized",
                    "device has been authorized",
                    "you can close this window",
                    "已授权",
                    "设备已授权",
                    "可以关闭",
                )
            ):
                result["ok"] = True
                result["reason"] = "confirmed"
                break

            qerr = parse_qs(parsed.query).get("error", [None])[0]
            if qerr == "rate_limited" or "rate limit" in text or "too many requests" in text:
                result["reason"] = "rate_limited"
                break
            if qerr:
                result["reason"] = f"device_verify_failed:{qerr}"
                break
            if ("attention required" in title and "cloudflare" in title) or "cloudflare ray id" in text:
                result["reason"] = "challenge_required"
                if headed:
                    await page.wait_for_timeout(1500)
                    continue
                break

            if await click_any(page, ("Reject all", "Reject All", "全部拒绝", "拒绝全部", "Accept all", "Accept All")):
                await page.wait_for_timeout(400)
                continue

            if any(m in text for m in ("continue with email", "sign in with email", "使用邮箱登录")) \
               or await page.locator('input[type="password"]').count():
                if not cookies and not sso:
                    result["reason"] = "login_required"
                    if headed:
                        print("[*] login required — complete login in browser window", flush=True)
                        await page.wait_for_timeout(2000)
                        continue
                    break
                result["reason"] = "login_required_sso_invalid"
                if headed:
                    print("[*] SSO/cookies not accepted — login manually if headed", flush=True)
                    await page.wait_for_timeout(2000)
                    continue
                break

            if "/oauth2/device/consent" in parsed.path or any(
                m in text for m in ("authorize grok", "授权 grok", "allow access", "请求访问", "wants to access")
            ):
                if not consent_done:
                    if await click_any(page, ("Allow", "Authorize", "Approve", "允许", "授权", "同意", "Continue", "继续")):
                        consent_done = True
                        await page.wait_for_timeout(900)
                        continue
                await page.wait_for_timeout(500)
                continue

            if await click_any(page, ("Allow", "Authorize", "Approve", "允许", "授权", "Confirm", "确认", "Continue", "继续")):
                await page.wait_for_timeout(800)
                continue

            await page.wait_for_timeout(700)

        if not result["ok"] and not result["reason"]:
            result["reason"] = "confirmation_timeout"

        if keep_open and headed:
            print("[*] keep-open enabled; Ctrl+C to stop", flush=True)
            try:
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass

        await context.close()
        await browser.close()
    return result


def build_parser():
    p = argparse.ArgumentParser(description="Browser device authorize helper for accounts.x.ai")
    p.add_argument("--sso", default="", help="SSO cookie value")
    p.add_argument("--source-file", default="", help="jsonl/tab source containing sso/cookies")
    p.add_argument("--source-index", type=int, default=0, help="jsonl line index (0-based)")
    p.add_argument("--verification-url", default="")
    p.add_argument("--user-code", default="")
    p.add_argument("--headed", action="store_true")
    p.add_argument("--timeout", type=float, default=120.0)
    p.add_argument("--keep-open", action="store_true")
    p.add_argument("--json-out", default="")
    return p


def load_source_line(path: Path, index: int = 0) -> dict:
    # if jsonl multi-line, pick index
    lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not lines:
        raise ValueError("empty source file")
    if index < 0 or index >= len(lines):
        raise ValueError(f"source-index out of range: {index}/{len(lines)}")
    # temporarily write one line for parser simplicity when multi
    one = lines[index]
    if one.startswith("{"):
        # reuse parser by writing temp content path-less
        obj = json.loads(one)
        tmp = Path(str(path) + f".line{index}.tmp")
        tmp.write_text(one + "\n", encoding="utf-8")
        try:
            return load_session_from_source_file(tmp)
        finally:
            try:
                tmp.unlink()
            except Exception:
                pass
    return load_session_from_source_file(path)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    cookies: list[dict] = []
    sso = (args.sso or "").strip() or None
    email = ""
    if args.source_file:
        sess = load_source_line(Path(args.source_file), args.source_index)
        email = sess.get("email") or ""
        sso = sso or (sess.get("sso") or None)
        cookies = list(sess.get("cookies") or [])
    if sso and not cookies:
        cookies = [
            {"name": "sso", "value": sso, "domain": d, "path": "/", "secure": True, "httpOnly": True}
            for d in ("accounts.x.ai", ".x.ai", "auth.x.ai", ".auth.x.ai")
        ]
    verification_url = build_verification_url(
        (args.user_code or "").strip() or None,
        (args.verification_url or "").strip() or None,
    )
    print(f"[*] email={email or '-'}", flush=True)
    print(f"[*] chrome={find_chrome()}", flush=True)
    print(f"[*] proxy={playwright_proxy_settings()}", flush=True)
    print(f"[*] cookies={len(cookies)} sso={'yes' if sso else 'no'} headed={args.headed}", flush=True)
    result = asyncio.run(
        authorize(
            cookies=cookies,
            sso=sso,
            verification_url=verification_url,
            user_code=(args.user_code or "").strip() or None,
            headed=bool(args.headed),
            timeout_sec=float(args.timeout),
            keep_open=bool(args.keep_open),
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
