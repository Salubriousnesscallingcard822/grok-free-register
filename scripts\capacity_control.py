from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROFILES_DIR = ROOT / "ops" / "capacity-profiles"
ENV_PATH = ROOT / ".env"
BACKUP_DIR = ROOT / "ops" / "capacity-backups"
STATE_PATH = ROOT / "ops" / "capacity-state.json"

MODES = ("safe", "balanced", "boost", "turbo")
CAPACITY_KEYS = {
    "PHYSICAL_CAP",
    "PHYSICAL_PER_CPU",
    "PHYSICAL_MEM_MB",
    "MIN_FREE_MEM_MB",
    "T_SLOT_CAP",
    "Q_SLOT_CAP",
    "Q_PENDING_CAP",
    "T_TARGET",
    "Q_TARGET",
    "P_BATCH_MAX",
    "P_SEND_CAP",
    "S_WORKERS",
    "P_WORKERS",
    "C_WORKERS",
    "C_HOT_PAGE_POOL",
    "C_HOT_PAGE_POOL_SIZE",
    "PAGE_POST_WAIT_MS",
    "CAPACITY_PROFILE",
    "REGISTER_CAPACITY_MODE",
}


def _read_env_file(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def _write_env_file(path: Path, values: dict[str, str]) -> None:
    existing_lines: list[str] = []
    if path.exists():
        existing_lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    seen: set[str] = set()
    out: list[str] = []
    for line in existing_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in values:
                out.append(f"{key}={values[key]}")
                seen.add(key)
                continue
        out.append(line)
    for key, value in values.items():
        if key not in seen:
            out.append(f"{key}={value}")
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def get_system_snapshot() -> dict:
    cpu = os.cpu_count() or 4
    total_mb = 8192
    free_mb = 2048
    if platform.system().lower().startswith("win"):
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                total_mb = max(1, int(stat.ullTotalPhys // (1024 * 1024)))
                free_mb = max(0, int(stat.ullAvailPhys // (1024 * 1024)))
        except Exception:
            pass
    load = max(0.0, min(100.0, 100.0 * (1.0 - (free_mb / max(total_mb, 1)))))
    return {
        "cpu": cpu,
        "total_mem_mb": total_mb,
        "free_mem_mb": free_mb,
        "mem_load_pct": round(load, 1),
    }


def recommend_mode(snapshot: dict | None = None) -> str:
    snap = snapshot or get_system_snapshot()
    free_mb = int(snap["free_mem_mb"])
    total_mb = int(snap["total_mem_mb"])
    free_ratio = free_mb / max(total_mb, 1)
    # Prefer free memory as the main throttle signal on this Windows host.
    if free_mb >= 6500 and free_ratio >= 0.35:
        return "turbo"
    if free_mb >= 4200 and free_ratio >= 0.25:
        return "boost"
    if free_mb >= 2200:
        return "balanced"
    return "safe"


def load_profile(mode: str) -> dict[str, str]:
    mode = (mode or "balanced").strip().lower()
    if mode not in MODES:
        raise SystemExit(f"unknown mode: {mode}; choose from {', '.join(MODES)}")
    path = PROFILES_DIR / f"{mode}.env"
    if not path.exists():
        raise SystemExit(f"missing profile: {path}")
    values = _read_env_file(path)
    values["REGISTER_CAPACITY_MODE"] = mode
    return values


def backup_env() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if ENV_PATH.exists():
        target = BACKUP_DIR / f".env.bak-{stamp}"
        shutil.copy2(ENV_PATH, target)
    else:
        target = BACKUP_DIR / f".env.missing-{stamp}"
        target.write_text("", encoding="utf-8")
    latest = BACKUP_DIR / "latest.env.bak"
    if ENV_PATH.exists():
        shutil.copy2(ENV_PATH, latest)
    return target


def apply_mode(mode: str, *, make_backup: bool = True) -> dict:
    profile = load_profile(mode)
    backup = backup_env() if make_backup else None
    current = _read_env_file(ENV_PATH)
    merged = dict(current)
    for key, value in profile.items():
        if key in CAPACITY_KEYS or key == "REGISTER_CAPACITY_MODE":
            merged[key] = value
    # keep non-capacity keys intact
    _write_env_file(ENV_PATH, {k: merged[k] for k in sorted(merged)})
    state = {
        "mode": mode,
        "applied_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "backup": str(backup) if backup else None,
        "profile": profile,
        "snapshot": get_system_snapshot(),
    }
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return state


def rollback(path: str | None = None) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if path:
        source = Path(path)
    else:
        # Prefer the backup recorded by the last successful apply.
        recorded = None
        if STATE_PATH.exists():
            try:
                recorded = json.loads(STATE_PATH.read_text(encoding="utf-8")).get("backup")
            except Exception:
                recorded = None
        candidates = []
        if recorded:
            candidates.append(Path(recorded))
        candidates.append(BACKUP_DIR / "latest.env.bak")
        candidates.extend(
            sorted(BACKUP_DIR.glob(".env.bak-*"), key=lambda p: p.stat().st_mtime, reverse=True)
        )
        source = next((c for c in candidates if c and c.exists()), None)
        if source is None:
            raise SystemExit("no capacity backup found")
    if not source.exists():
        raise SystemExit(f"backup not found: {source}")

    # Freeze the restore payload first so later backup_env() cannot clobber it.
    restore_snapshot = BACKUP_DIR / f".env.restore-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(source, restore_snapshot)

    # Keep a safety copy of the current env, then restore the frozen snapshot.
    safety = backup_env()
    shutil.copy2(restore_snapshot, ENV_PATH)
    state = {
        "mode": "rollback",
        "applied_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "restored_from": str(source),
        "restore_snapshot": str(restore_snapshot),
        "pre_rollback_backup": str(safety),
        "snapshot": get_system_snapshot(),
    }
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return source

def describe() -> dict:
    snap = get_system_snapshot()
    current = _read_env_file(ENV_PATH)
    recommended = recommend_mode(snap)
    state = {}
    if STATE_PATH.exists():
        try:
            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            state = {}
    return {
        "snapshot": snap,
        "recommended_mode": recommended,
        "current_mode": current.get("REGISTER_CAPACITY_MODE") or state.get("mode") or "unknown",
        "current_physical_cap": current.get("PHYSICAL_CAP"),
        "state": state,
        "profiles": {m: str(PROFILES_DIR / f"{m}.env") for m in MODES},
        "rollback_latest": str(BACKUP_DIR / "latest.env.bak"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Grok register adaptive capacity controller")
    parser.add_argument("action", choices=["status", "recommend", "apply", "rollback", "list"])
    parser.add_argument("--mode", choices=list(MODES), default=None)
    parser.add_argument("--backup", default=None, help="explicit backup path for rollback")
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args(argv)

    if args.action == "list":
        for mode in MODES:
            print(mode, PROFILES_DIR / f"{mode}.env")
        return 0
    if args.action == "status":
        print(json.dumps(describe(), ensure_ascii=False, indent=2))
        return 0
    if args.action == "recommend":
        snap = get_system_snapshot()
        mode = recommend_mode(snap)
        print(json.dumps({"recommended_mode": mode, "snapshot": snap}, ensure_ascii=False, indent=2))
        return 0
    if args.action == "apply":
        mode = args.mode or recommend_mode()
        state = apply_mode(mode, make_backup=not args.no_backup)
        print(json.dumps({"ok": True, "applied": state}, ensure_ascii=False, indent=2))
        return 0
    if args.action == "rollback":
        source = rollback(args.backup)
        print(json.dumps({"ok": True, "restored_from": str(source)}, ensure_ascii=False, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
