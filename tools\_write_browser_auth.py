from pathlib import Path
root = Path(r".")

# 1) Fix playwright proxy credential parsing
executors = root / "xai_enroller" / "executors.py"
t = executors.read_text(encoding="utf-8")
old = '''def _playwright_proxy_settings():
    proxy = _env_proxy_url()
    if not proxy:
        return None
    # Playwright expects server without credentials embedded sometimes, but full URL is accepted.
    return {"server": proxy}
'''
new = '''def _playwright_proxy_settings():
    """Return Playwright proxy dict.

    Playwright often fails auth when credentials are embedded in server URL
    (net::ERR_INVALID_AUTH_CREDENTIALS). Split user:pass into username/password.
    """
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
    server = f"{scheme}://{parsed.hostname}{port}"
    settings = {"server": server}
    if parsed.username:
        settings["username"] = parsed.username
    if parsed.password is not None:
        settings["password"] = parsed.password
    return settings
'''
if old in t:
    t = t.replace(old, new)
    executors.write_text(t, encoding="utf-8")
    print("patched executors proxy")
else:
    print("executors proxy pattern not found; leave as-is")

# 2) Improve oauth_rejected diagnostics in protocol.py
proto = root / "xai_enroller" / "protocol.py"
pt = proto.read_text(encoding="utf-8")
old_r = '''            if error == "access_denied":
                raise RuntimeError("oauth_denied")
            if error == "expired_token":
                raise RuntimeError("oauth_expired")
            raise RuntimeError("oauth_rejected")
'''
new_r = '''            if error == "access_denied":
                raise RuntimeError("oauth_denied")
            if error == "expired_token":
                raise RuntimeError("oauth_expired")
            # Keep machine-readable prefix for pipeline mapping; append detail for logs.
            detail = error or f"http_{response.status_code}"
            desc = document.get("error_description") or document.get("message") or ""
            if desc:
                detail = f"{detail}:{desc}"
            raise RuntimeError(f"oauth_rejected:{detail}")
'''
if old_r in pt:
    pt = pt.replace(old_r, new_r)
    # mapping uses exact reason oauth_rejected - fix auth_pipeline to startswith
    proto.write_text(pt, encoding="utf-8")
    print("patched protocol oauth detail")
else:
    print("protocol pattern not found")

# auth_pipeline mapping should accept oauth_rejected:* 
ap = root / "xai_enroller" / "auth_pipeline.py"
at = ap.read_text(encoding="utf-8")
old_map = '''            mapping = {
                "oauth_denied": JobStatus.OAUTH_DENIED,
                "oauth_expired": JobStatus.OAUTH_EXPIRED,
                "oauth_rejected": JobStatus.OAUTH_REJECTED,
            }
            status = mapping.get(reason)
'''
new_map = '''            mapping = {
                "oauth_denied": JobStatus.OAUTH_DENIED,
                "oauth_expired": JobStatus.OAUTH_EXPIRED,
                "oauth_rejected": JobStatus.OAUTH_REJECTED,
            }
            status = mapping.get(reason)
            if status is None:
                for key, value in mapping.items():
                    if reason.startswith(key):
                        status = value
                        break
'''
if old_map in at:
    at = at.replace(old_map, new_map)
    ap.write_text(at, encoding="utf-8")
    print("patched auth_pipeline reason mapping")
else:
    print("auth_pipeline mapping not found")

# 3) Write browser authorize script
script = root / "scripts" / "browser_device_authorize.py"
script.write_text(r'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Browser OAuth Device Authorization for accounts.x.ai.

xAI moved device approval onto the accounts.x.ai web front-end.
This script:
  1) launches Chromium (CloakBrowser if available)
  2) injects SSO cookie (or opens login page)
  3) opens verification_url / fills user_code
  4) clicks Allow / Authorize
  5) waits until "device authorized" / /oauth2/device/done

Examples:
  # with SSO token + verification URL
  python scripts/browser_device_authorize.py ^
    --sso "eyJ..." ^
    --verification-url "https://accounts.x.ai/sign-in?..." 

  # with user_code only (script builds URL)
  python scripts/browser_device_authorize.py --sso "..." --user-code "ABCD-EFGH"

  # source line file: id<TAB>sso_token  (first line)
  python scripts/browser_device_authorize.py --source-file auth-local/source-snapshot.jsonl --user-code ABCD-EFGH

  # headed for manual assist
  python scripts/browser_device_authorize.py --sso ... --user-code ... --headed
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlencode, urlunparse

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
    # project-local cloak cache
    for cand in (ROOT / ".cloakbrowser").rglob("chrome.exe"):
        return str(cand)
    return None


def load_sso_from_source_file(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    # jsonl style: {"sso":...} or {"token":...} or id\tsso
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("{"):
            try:
                obj = json.loads(s)
            except Exception:
                continue
            for k in ("sso", "sso_token", "token", "cookie"):
                v = obj.get(k)
                if isinstance(v, str) and v:
                    return v
            cookies = obj.get("cookies")
            if isinstance(cookies, list):
                for c in cookies:
                    if isinstance(c, dict) and c.get("name") == "sso" and c.get("value"):
                        return str(c["value"])
            continue
        if "\t" in s:
            _id, token = s.split("\t", 1)
            if token.strip():
                return token.strip()
        if s.count("----") >= 1 and "@" not in s.split("----")[0]:
            # raw sso alone
            return s
        # maybe pure sso token line
        if len(s) > 20 and " " not in s:
            return s
    raise ValueError(f"no sso token found in {path}")


def build_verification_url(user_code: str | None, verification_url: str | None) -> str:
    if verification_url:
        return verification_url
    if not user_code:
        raise ValueError("need --verification-url or --user-code")
    # accounts.x.ai device verify entry (common form)
    base = "https://accounts.x.ai/sign-in"
    # many flows accept user_code query; also support auth.x.ai complete URL if provided elsewhere
    q = urlencode({"user_code": user_code})
    return f"https://accounts.x.ai/oauth2/device/verify?{q}"


def sso_cookies(sso: str) -> list[dict]:
    cookies = []
    for domain in ("accounts.x.ai", ".x.ai", "auth.x.ai", ".auth.x.ai"):
        cookies.append({
            "name": "sso",
            "value": sso,
            "domain": domain if domain.startswith(".") else domain,
            "path": "/",
            "secure": True,
            "httpOnly": True,
        })
    return cookies


async def click_any(page, names: tuple[str, ...]) -> bool:
    for name in names:
        try:
            btn = page.get_by_role("button", name=name, exact=True)
            if await btn.count() and await btn.first.is_visible():
                await btn.first.click()
                return True
        except Exception:
            pass
        try:
            loc = page.locator(f'button:has-text("{name}"), input[type="submit"][value="{name}"]')
            if await loc.count() and await loc.first.is_visible():
                await loc.first.click()
                return True
        except Exception:
            pass
    return False


async def authorize(
    *,
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
    }

    async with async_playwright() as pw:
        launch_opts = {"headless": not headed}
        if chrome:
            launch_opts["executable_path"] = chrome
        if proxy:
            # also set at launch-level for some cloak builds
            launch_opts["proxy"] = proxy
        browser = await pw.chromium.launch(**launch_opts)
        context_opts = {}
        if proxy:
            context_opts["proxy"] = proxy
        context = await browser.new_context(**context_opts)
        if sso:
            await context.add_cookies(sso_cookies(sso))
        page = await context.new_page()
        print(f"[*] goto {verification_url}", flush=True)
        await page.goto(verification_url, wait_until="domcontentloaded", timeout=60000)

        # if user_code provided and input exists, fill
        if user_code:
            try:
                code_input = page.locator('input[name="user_code"], input[autocomplete="one-time-code"], input[type="text"]')
                if await code_input.count():
                    el = code_input.first
                    if await el.is_visible():
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
            if parsed.scheme != "https" or not host.endswith("x.ai"):
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

            # success markers
            if "/oauth2/device/done" in parsed.path or any(
                m in text for m in (
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
            if "cloudflare" in title and "attention required" in title:
                result["reason"] = "challenge_required"
                if headed:
                    await page.wait_for_timeout(1500)
                    continue
                break

            # cookie banner
            if await click_any(page, ("Reject all", "Reject All", "全部拒绝", "拒绝全部", "Accept all", "Accept All")):
                await page.wait_for_timeout(500)
                continue

            # login required
            if any(m in text for m in ("continue with email", "sign in with email", "使用邮箱登录", "sign in")) \
               or await page.locator('input[type="password"]').count():
                if not sso:
                    result["reason"] = "login_required"
                    if headed:
                        print("[*] login required — complete login in the open browser...", flush=True)
                        await page.wait_for_timeout(2000)
                        continue
                    break
                # sso present but still login page => cookie not accepted
                # try visit accounts root then return
                await page.goto("https://accounts.x.ai/", wait_until="domcontentloaded")
                await page.goto(verification_url, wait_until="domcontentloaded")
                await page.wait_for_timeout(800)
                # if still login, report
                text2 = ""
                try:
                    text2 = (await page.locator("body").inner_text(timeout=3000)).lower()
                except Exception:
                    pass
                if any(m in text2 for m in ("continue with email", "sign in with email", "使用邮箱登录")):
                    result["reason"] = "login_required_sso_invalid"
                    if headed:
                        await page.wait_for_timeout(2000)
                        continue
                    break
                continue

            # consent
            if "/oauth2/device/consent" in parsed.path or any(
                m in text for m in ("authorize grok", "授权 grok", "allow access", "请求访问")
            ):
                if not consent_done:
                    if await click_any(page, ("Allow", "Authorize", "Approve", "允许", "授权", "同意", "Continue")):
                        consent_done = True
                        await page.wait_for_timeout(1000)
                        continue
                await page.wait_for_timeout(500)
                continue

            # generic allow
            if await click_any(page, ("Allow", "Authorize", "Approve", "允许", "授权", "Confirm", "确认")):
                await page.wait_for_timeout(800)
                continue

            await page.wait_for_timeout(700)

        if not result["ok"] and not result["reason"]:
            result["reason"] = "confirmation_timeout"

        if keep_open and headed:
            print("[*] keep-open: press Ctrl+C in terminal to close", flush=True)
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
    p.add_argument("--source-file", default="", help="file with id\\tsso or jsonl containing sso")
    p.add_argument("--verification-url", default="", help="full verification URL")
    p.add_argument("--user-code", default="", help="device user_code like ABCD-EFGH")
    p.add_argument("--headed", action="store_true", help="show browser window")
    p.add_argument("--timeout", type=float, default=90.0)
    p.add_argument("--keep-open", action="store_true")
    p.add_argument("--json-out", default="", help="write result json path")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    sso = (args.sso or "").strip() or None
    if not sso and args.source_file:
        sso = load_sso_from_source_file(Path(args.source_file))
    verification_url = build_verification_url(
        (args.user_code or "").strip() or None,
        (args.verification_url or "").strip() or None,
    )
    print(f"[*] chrome={find_chrome()}", flush=True)
    print(f"[*] proxy={playwright_proxy_settings()}", flush=True)
    print(f"[*] sso={'yes' if sso else 'no'} headed={args.headed}", flush=True)
    result = asyncio.run(
        authorize(
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
''', encoding="utf-8")
print("wrote", script)

# docs snippet
doc = root / "docs" / "guides" / "browser-device-authorize.md"
doc.parent.mkdir(parents=True, exist_ok=True)
doc.write_text('''# Browser Device Authorize (accounts.x.ai)

xAI 把 device OAuth 批准放到了 `accounts.x.ai` 网页端。服务端 `auth.x.ai` 只能申请 device_code / 轮询 token，**批准必须浏览器完成**。

## 脚本

```bash
cd grok-free-register-main
.venv\\Scripts\\python.exe scripts\\browser_device_authorize.py --help
```

### 最小用法

```bash
.venv\\Scripts\\python.exe scripts\\browser_device_authorize.py ^
  --sso "你的sso" ^
  --user-code "ABCD-EFGH" ^
  --headed
```

或完整 verification URL：

```bash
.venv\\Scripts\\python.exe scripts\\browser_device_authorize.py ^
  --sso "你的sso" ^
  --verification-url "https://accounts.x.ai/..." ^
  --headed
```

### 代理

读环境变量 / `.env`：

```env
HTTP_PROXY=http://user:pass@host:port
HTTPS_PROXY=http://user:pass@host:port
```

脚本会把 `user:pass` 拆成 Playwright 的 `username/password`，避免 `ERR_INVALID_AUTH_CREDENTIALS`。

### 和 enroller 的关系

- enroller：`device_code` 申请 + `poll_token`
- 本脚本：浏览器打开 `verification_url`，登录/注入 SSO，点 Allow
- 两边用同一个 `user_code/device_code` 窗口配对

当 `poll_token` 报 `oauth_rejected` 时，优先检查：

1. 浏览器是否真的到了 device authorized 页
2. SSO 是否仍有效（是否变成 login_required）
3. 代理是否让浏览器和 httpx 看到同一出口
''', encoding="utf-8")
print("wrote", doc)
print("done")
