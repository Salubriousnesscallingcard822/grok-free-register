#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Path B auth daemon for start-auth-windows.ps1

Continuously converts source sessions (cookies/SSO) into
auth-local/authenticated/xai-*.json via accounts.x.ai browser approval.

State file tracks done emails so restarts skip completed accounts.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

from scripts.device_flow_browser_complete import run_once  # type: ignore


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    n = 0
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if line.strip():
                n += 1
    return n


def load_done(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {ln.strip() for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip()}


def append_done(path: Path, key: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(key + "\n")


def email_from_line(line: str) -> str:
    s = line.strip()
    if not s:
        return ""
    if s.startswith("{"):
        try:
            return str(json.loads(s).get("email") or "")
        except Exception:
            return ""
    if "\t" in s:
        return s.split("\t", 1)[0].strip()
    return ""


def pick_next_index(source: Path, done: set[str], start_from_end: bool, scan_window: int) -> int | None:
    """Return next line index not marked done. Prefer recent lines."""
    lines: list[str] = []
    with source.open("r", encoding="utf-8", errors="ignore") as f:
        for ln in f:
            if ln.strip():
                lines.append(ln)
    if not lines:
        return None
    total = len(lines)
    if start_from_end:
        begin = max(0, total - max(scan_window, 1))
        order = list(range(total - 1, begin - 1, -1)) + list(range(0, begin))
    else:
        order = list(range(total))
    for idx in order:
        email = email_from_line(lines[idx])
        key = email or f"idx:{idx}"
        if key in done or f"idx:{idx}" in done:
            continue
        return idx
    return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Path B continuous auth daemon")
    p.add_argument("--source-file", default=str(ROOT / "auth-local" / "source-snapshot.jsonl"))
    p.add_argument("--state-file", default=str(ROOT / "keys" / "pathb-auth-done.txt"))
    p.add_argument("--count", type=int, default=0, help="0=run forever until idle; >0 stop after N successes")
    p.add_argument("--max-attempts", type=int, default=0, help="0=unlimited attempts")
    p.add_argument("--idle-sleep", type=float, default=20.0, help="sleep when no pending source")
    p.add_argument("--fail-sleep", type=float, default=8.0)
    p.add_argument("--ok-sleep", type=float, default=2.0)
    p.add_argument("--browser-timeout", type=float, default=120.0)
    p.add_argument("--poll-timeout", type=float, default=180.0)
    p.add_argument("--headed", action="store_true")
    p.add_argument("--scan-window", type=int, default=500, help="prefer newest N lines first")
    p.add_argument("--from-start", action="store_true", help="scan from index 0 instead of newest")
    args = p.parse_args(argv)

    source = Path(args.source_file)
    # fallback sessions
    if not source.exists():
        alt = ROOT / "keys" / "auth-sessions.jsonl"
        if alt.exists():
            source = alt
            log(f"source missing; fallback {source}")
    if not source.exists():
        log(f"FATAL missing source file: {args.source_file}")
        return 2

    state = Path(args.state_file)
    done = load_done(state)
    ok_n = fail_n = attempts = 0
    target_ok = int(args.count) if args.count > 0 else 0
    max_attempts = int(args.max_attempts) if args.max_attempts > 0 else 0

    log(
        f"PathB daemon source={source} lines~{count_lines(source)} done={len(done)} "
        f"headed={bool(args.headed)} target_ok={target_ok or 'inf'}"
    )

    while True:
        if target_ok and ok_n >= target_ok:
            log(f"reached target ok={ok_n}; exit")
            break
        if max_attempts and attempts >= max_attempts:
            log(f"reached max attempts={attempts}; exit")
            break

        idx = pick_next_index(source, done, start_from_end=not args.from_start, scan_window=args.scan_window)
        if idx is None:
            log(f"no pending source; sleep {args.idle_sleep}s (done={len(done)} ok={ok_n} fail={fail_n})")
            time.sleep(max(1.0, args.idle_sleep))
            # reload done in case external tools marked more
            done = load_done(state)
            continue

        attempts += 1
        log(f"=== attempt={attempts} index={idx} ok={ok_n} fail={fail_n} ===")
        try:
            result = asyncio.run(
                run_once(
                    source_file=source,
                    source_index=idx,
                    headed=bool(args.headed),
                    timeout_sec=float(args.browser_timeout),
                    poll_timeout=float(args.poll_timeout),
                    json_out=None,
                )
            )
        except Exception as e:
            result = {"ok": False, "error": f"{type(e).__name__}:{e}", "email": ""}

        email = (result.get("email") or "").strip()
        key = email or f"idx:{idx}"
        if result.get("ok"):
            ok_n += 1
            append_done(state, key)
            done.add(key)
            done.add(f"idx:{idx}")
            append_done(state, f"idx:{idx}")
            log(f"OK {key} -> {result.get('authenticated_json') or 'authenticated'}")
            time.sleep(max(0.0, args.ok_sleep))
        else:
            fail_n += 1
            err = str(result.get("error") or "failed")
            log(f"FAIL {key} err={err}")
            # permanent-ish failures: mark done so we don't spin
            if any(x in err for x in ("login_required", "sso_invalid", "no session", "source has no")):
                append_done(state, key)
                done.add(key)
                append_done(state, f"idx:{idx}")
                done.add(f"idx:{idx}")
                log(f"mark dead {key}")
            time.sleep(max(0.0, args.fail_sleep))

    summary = {"ok": ok_n, "fail": fail_n, "attempts": attempts, "done_keys": len(done)}
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if ok_n > 0 or target_ok == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
