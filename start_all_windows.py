#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run_ps(script: str, args=None, background: bool = False) -> int:
    script_path = ROOT / script
    if not script_path.exists():
        raise SystemExit(f"missing {script_path}")
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
    ]
    if args:
        cmd.extend(args)
    env = os.environ.copy()
        if env.get("HTTP_PROXY") and not env.get("HTTPS_PROXY"):
        env["HTTPS_PROXY"] = env["HTTP_PROXY"]
    env["CLOAKBROWSER_CACHE_DIR"] = str(ROOT / ".cloakbrowser")
    if background:
        subprocess.Popen(cmd, cwd=str(ROOT), env=env)
        return 0
    return subprocess.call(cmd, cwd=str(ROOT), env=env)


def py_matches(pattern: str):
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" | "
        "Where-Object { $_.CommandLine -and ($_.CommandLine -match '" + pattern + "') } | "
        "ForEach-Object { $_.ProcessId }"
    )
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", ps],
            text=True,
            errors="ignore",
        )
    except Exception:
        return []
    return [int(x.strip()) for x in out.splitlines() if x.strip().isdigit()]


def status() -> None:
    print("=== grok-free-register stack ===")
    print("root:", ROOT)
    print("register :", len(py_matches(r"grok_register\\.register")))
    print("pathb    :", len(py_matches(r"auth_pathb_daemon|device_flow_browser_complete")))
    print("import   :", len(py_matches(r"import_authenticated_to_grok2api|_import_watch")))
    print("email    :", len(py_matches(r"grok_register\\.email_server")))
    auth = ROOT / "auth-local" / "authenticated"
    if auth.exists():
        print("authjson :", len(list(auth.glob("xai-*.json"))))
    try:
        print("grok2api : up", urllib.request.urlopen("http://127.0.0.1:8000/healthz", timeout=3).status)
    except Exception:
        print("grok2api : down")
    try:
        print("mail     :", urllib.request.urlopen("http://127.0.0.1:8088/health", timeout=3).read().decode())
    except Exception:
        print("mail     : down")
    print("cloak    :", ROOT / ".cloakbrowser")
    print("docs     : docs/guides/cloakbrowser-and-import.md")


def stop() -> None:
    patterns = [
        r"grok_register\\.register",
        r"auth_pathb_daemon",
        r"device_flow_browser_complete",
        r"import_authenticated_to_grok2api",
        r"_import_watch",
    ]
    for pat in patterns:
        for pid in py_matches(pat):
            print("stop", pid, pat)
            subprocess.call(
                ["taskkill", "/F", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Unified entry for CloakBrowser + PathB + grok2api import"
    )
    parser.add_argument(
        "action",
        nargs="?",
        default="status",
        choices=["status", "up", "register", "auth", "import", "full", "mail", "stop", "help"],
    )
    parser.add_argument("--auth-count", type=int, default=0)
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args(argv)

    if args.action == "help":
        print("status|up|register|auth|import|full|mail|stop")
        print("docs/guides/cloakbrowser-and-import.md")
        return 0
    if args.action == "status":
        status()
        return 0
    if args.action == "register":
        return run_ps("start-windows.ps1")
    if args.action == "auth":
        auth_args = []
        if args.auth_count > 0:
            auth_args += ["-AuthCount", str(args.auth_count)]
        if args.headed:
            auth_args += ["-Headed"]
        return run_ps("start-auth-windows.ps1", auth_args)
    if args.action == "import":
        return run_ps("start-full-to-grok2api.ps1", ["import-only"])
    if args.action == "full":
        return run_ps("start-full-to-grok2api.ps1", ["all"])
    if args.action == "mail":
        return run_ps("start-email-service-windows.ps1")
    if args.action == "up":
        print("[*] starting register")
        run_ps("start-windows.ps1", background=True)
        print("[*] starting Path B auth background")
        run_ps("start-auth-windows.ps1", ["-Background"])
        time.sleep(2)
        print("[*] import pass")
        run_ps("start-full-to-grok2api.ps1", ["import-only"])
        status()
        print("[+] up complete")
        return 0
    if args.action == "stop":
        stop()
        print("[+] stop done")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
