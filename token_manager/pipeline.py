from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _now() -> float:
    return time.time()


def _read_text(path: Path, limit: int = 200_000) -> str:
    try:
        data = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(data) > limit:
        return data[-limit:]
    return data


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    try:
        with path.open("rb") as handle:
            for _ in handle:
                count += 1
    except OSError:
        return 0
    return count


def _count_json_files(directory: Path) -> int:
    if not directory.exists():
        return 0
    try:
        return sum(1 for path in directory.glob("*.json") if path.is_file())
    except OSError:
        return 0


def _tail_lines(path: Path, limit: int = 40) -> list[str]:
    text = _read_text(path)
    if not text:
        return []
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    return lines[-limit:]


@dataclass
class ManagedProcess:
    name: str
    command: list[str]
    cwd: Path
    env: dict[str, str]
    stdout_path: Path
    stderr_path: Path
    pid_path: Path
    process: subprocess.Popen[Any] | None = None
    started_at: float | None = None
    last_error: str | None = None
    _stdout_handle: Any = field(default=None, repr=False)
    _stderr_handle: Any = field(default=None, repr=False)

    def is_running(self) -> bool:
        if self.process is None:
            return False
        code = self.process.poll()
        if code is None:
            return True
        self._close_handles()
        return False

    def pid(self) -> int | None:
        if self.process is None:
            return None
        return self.process.pid

    def start(self) -> dict[str, Any]:
        if self.is_running():
            return {"ok": True, "already_running": True, "pid": self.pid()}
        self.stdout_path.parent.mkdir(parents=True, exist_ok=True)
        self.stderr_path.parent.mkdir(parents=True, exist_ok=True)
        self.pid_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._stdout_handle = self.stdout_path.open("a", encoding="utf-8", errors="replace")
            self._stderr_handle = self.stderr_path.open("a", encoding="utf-8", errors="replace")
            creationflags = CREATE_NO_WINDOW if os.name == "nt" else 0
            self.process = subprocess.Popen(
                self.command,
                cwd=str(self.cwd),
                env=self.env,
                stdout=self._stdout_handle,
                stderr=self._stderr_handle,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            self.started_at = _now()
            self.last_error = None
            self.pid_path.write_text(str(self.process.pid), encoding="utf-8")
            return {"ok": True, "already_running": False, "pid": self.process.pid}
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            self._close_handles()
            return {"ok": False, "error": str(exc)}

    def stop(self, timeout: float = 8.0) -> dict[str, Any]:
        if not self.is_running() or self.process is None:
            self._close_handles()
            return {"ok": True, "already_stopped": True}
        process = self.process
        try:
            if os.name == "nt":
                process.terminate()
            else:
                process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            code = process.returncode
            self._close_handles()
            self.process = None
            return {"ok": True, "already_stopped": False, "exit_code": code}
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            return {"ok": False, "error": str(exc)}

    def _close_handles(self) -> None:
        for handle in (self._stdout_handle, self._stderr_handle):
            if handle is None:
                continue
            try:
                handle.close()
            except OSError:
                pass
        self._stdout_handle = None
        self._stderr_handle = None

    def snapshot(self) -> dict[str, Any]:
        running = self.is_running()
        return {
            "name": self.name,
            "running": running,
            "pid": self.pid() if running else None,
            "started_at": self.started_at,
            "uptime_seconds": round(_now() - self.started_at, 1) if running and self.started_at else 0,
            "command": self.command,
            "stdout_path": str(self.stdout_path),
            "stderr_path": str(self.stderr_path),
            "last_error": self.last_error,
            "recent_stdout": _tail_lines(self.stdout_path, 20),
            "recent_stderr": _tail_lines(self.stderr_path, 12),
        }


class PipelineController:
    """Local register + auth process manager for Grok Tool."""

    def __init__(self, project_root: Path):
        self.project_root = Path(project_root).resolve()
        self.logs_dir = self.project_root / "logs"
        self.keys_dir = self.project_root / "keys"
        self.auth_local = self.project_root / "auth-local"
        self.authenticated_dir = self.auth_local / "authenticated"
        self.claimed_dir = self.auth_local / "claimed"
        self.ledger_path = self.auth_local / "enrollment-ledger.db"
        self._lock = threading.RLock()
        self._register: ManagedProcess | None = None
        self._auth: ManagedProcess | None = None
        self.enabled = self._detect_enabled()

    def _detect_enabled(self) -> bool:
        if getattr(sys, "frozen", False):
            return False
        register_mod = self.project_root / "grok_register" / "register.py"
        auth_mod = self.project_root / "xai_enroller" / "service.py"
        return register_mod.exists() and auth_mod.exists()

    def _python(self) -> str:
        venv_py = self.project_root / ".venv" / "Scripts" / "python.exe"
        if venv_py.exists():
            return str(venv_py)
        venv_py = self.project_root / ".venv" / "bin" / "python"
        if venv_py.exists():
            return str(venv_py)
        return sys.executable

    def _base_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.setdefault("HTTP_PROXY", "http://127.0.0.1:7897")
        env.setdefault("HTTPS_PROXY", "http://127.0.0.1:7897")
        cloak_dir = self.project_root / ".cloakbrowser"
        env["CLOAKBROWSER_CACHE_DIR"] = str(cloak_dir)
        env["PYTHONUNBUFFERED"] = "1"
        env["XAI_AUTH_SERVICE_SOURCE"] = env.get("XAI_AUTH_SERVICE_SOURCE") or "local"
        env["XAI_AUTH_SERVICE_REGISTER_ROOT"] = str(self.project_root)
        env["XAI_ENROLLER_LOCAL_AUTH_DIR"] = str(self.auth_local)
        env["XAI_AUTH_SERVICE_DAEMON"] = "1"
        env["XAI_AUTH_SERVICE_LOG_MODE"] = env.get("XAI_AUTH_SERVICE_LOG_MODE") or "user"
        env["XAI_AUTH_SERVICE_MIN_INTERVAL_SEC"] = env.get("XAI_AUTH_SERVICE_MIN_INTERVAL_SEC") or "8"
        env["XAI_AUTH_SERVICE_RETRY_SEC"] = env.get("XAI_AUTH_SERVICE_RETRY_SEC") or "45"
        env["XAI_AUTH_SERVICE_SYNC_SEC"] = env.get("XAI_AUTH_SERVICE_SYNC_SEC") or "20"
        # Prefer project cloakbrowser chromium for auth convert.
        browser = env.get("XAI_ENROLLER_BROWSER_EXECUTABLE")
        if not browser:
            candidates = sorted(cloak_dir.glob("chromium-*/chrome.exe"))
            if candidates:
                env["XAI_ENROLLER_BROWSER_EXECUTABLE"] = str(candidates[-1])
        env.setdefault("ALL_PROXY", env.get("HTTPS_PROXY") or env.get("HTTP_PROXY") or "http://127.0.0.1:7897")
        env.setdefault("XAI_ENROLLER_TIMEOUT_SEC", "240")
        env.setdefault("XAI_ENROLLER_POLL_SEC", "3")
        return env

    def _ensure_dirs(self) -> None:
        for path in (
            self.logs_dir,
            self.keys_dir,
            self.auth_local,
            self.authenticated_dir,
            self.claimed_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def _find_external(self, pattern: str) -> list[dict[str, Any]]:
        """Best-effort external process discovery.

        Prefer psutil. On Windows, avoid full Get-CimInstance scans by default
        because they can hang/OOM under load; enable with PIPELINE_SCAN_EXTERNAL=1.
        """
        matches: list[dict[str, Any]] = []
        try:
            import psutil  # type: ignore

            for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                try:
                    cmdline = " ".join(proc.info.get("cmdline") or [])
                except (psutil.Error, TypeError):
                    continue
                if pattern in cmdline:
                    matches.append({"pid": proc.info.get("pid"), "cmdline": cmdline[:220]})
            return matches
        except Exception:
            pass

        if os.name != "nt":
            return matches
        if (os.environ.get("PIPELINE_SCAN_EXTERNAL") or "").strip().lower() not in {"1", "true", "yes"}:
            return matches
        try:
            creationflags = CREATE_NO_WINDOW
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    (
                        "Get-CimInstance Win32_Process | "
                        "Where-Object { $_.CommandLine -match '"
                        + pattern.replace("'", "''")
                        + "' } | "
                        "Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress"
                    ),
                ],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=creationflags,
            )
            raw = (completed.stdout or "").strip()
            if not raw:
                return matches
            data = json.loads(raw)
            if isinstance(data, dict):
                data = [data]
            for item in data or []:
                matches.append(
                    {
                        "pid": item.get("ProcessId"),
                        "cmdline": str(item.get("CommandLine") or "")[:220],
                    }
                )
        except Exception:
            return matches
        return matches

    def _attach_or_create_register(self) -> ManagedProcess:
        if self._register is None:
            env = self._base_env()
            self._register = ManagedProcess(
                name="register",
                command=[self._python(), "-u", "-X", "faulthandler", "-m", "grok_register.register"],
                cwd=self.project_root,
                env=env,
                stdout_path=self.logs_dir / "register-tool.out.log",
                stderr_path=self.logs_dir / "register-tool.err.log",
                pid_path=self.logs_dir / "register-tool.pid",
            )
        return self._register

    def _attach_or_create_auth(self) -> ManagedProcess:
        if self._auth is None:
            env = self._base_env()
            self._auth = ManagedProcess(
                name="auth",
                command=[self._python(), "-u", "-m", "xai_enroller.service"],
                cwd=self.project_root,
                env=env,
                stdout_path=self.logs_dir / "auth-tool.out.log",
                stderr_path=self.logs_dir / "auth-tool.err.log",
                pid_path=self.logs_dir / "auth-tool.pid",
            )
        return self._auth

    def start_register(self) -> dict[str, Any]:
        with self._lock:
            if not self.enabled:
                return {"ok": False, "error": "pipeline control unavailable in portable exe"}
            self._ensure_dirs()
            external = self._find_external(r"grok_register\.register")
            managed = self._attach_or_create_register()
            if managed.is_running():
                return {"ok": True, "already_running": True, "pid": managed.pid(), "managed": True}
            if external:
                return {
                    "ok": True,
                    "already_running": True,
                    "managed": False,
                    "external": external,
                    "message": "register already running outside Grok Tool",
                }
            return managed.start()

    def stop_register(self) -> dict[str, Any]:
        with self._lock:
            managed = self._attach_or_create_register()
            result = managed.stop()
            # Best-effort stop for externally launched process is intentionally skipped.
            return result

    def start_auth(self) -> dict[str, Any]:
        with self._lock:
            if not self.enabled:
                return {"ok": False, "error": "pipeline control unavailable in portable exe"}
            self._ensure_dirs()
            external = self._find_external(r"xai_enroller\.service")
            managed = self._attach_or_create_auth()
            if managed.is_running():
                return {"ok": True, "already_running": True, "pid": managed.pid(), "managed": True}
            if external:
                return {
                    "ok": True,
                    "already_running": True,
                    "managed": False,
                    "external": external,
                    "message": "auth already running outside Grok Tool",
                }
            return managed.start()

    def stop_auth(self) -> dict[str, Any]:
        with self._lock:
            managed = self._attach_or_create_auth()
            return managed.stop()

    def start_all(self) -> dict[str, Any]:
        return {
            "register": self.start_register(),
            "auth": self.start_auth(),
        }

    def stop_all(self) -> dict[str, Any]:
        return {
            "register": self.stop_register(),
            "auth": self.stop_auth(),
        }

    def _inventory_counts(self) -> dict[str, int]:
        counts = {"available": 0, "claiming": 0, "claimed": 0}
        if not self.ledger_path.exists():
            return counts
        try:
            import sqlite3

            with sqlite3.connect(str(self.ledger_path)) as conn:
                rows = conn.execute(
                    "SELECT state, COUNT(*) AS count FROM credential_inventory GROUP BY state"
                )
                for state, count in rows:
                    counts[str(state)] = int(count)
        except Exception:
            return counts
        return counts

    def _register_rate_hint(self) -> str | None:
        log_candidates = [
            self.logs_dir / "register-tool.out.log",
            self.logs_dir / "register-direct.out.log",
        ]
        for path in log_candidates:
            for line in reversed(_tail_lines(path, 80)):
                if "rate:" in line and "ok:" in line:
                    # e.g. ok:2732 fail:48 rate:4.0/min #2732
                    try:
                        chunk = line.split("rate:", 1)[1].strip().split()[0]
                        return chunk
                    except Exception:
                        return line[-80:]
        return None

    def status(self) -> dict[str, Any]:
        with self._lock:
            register = self._attach_or_create_register()
            auth = self._attach_or_create_auth()
            register_snap = register.snapshot()
            auth_snap = auth.snapshot()
            external_register = []
            external_auth = []
            if not register_snap["running"]:
                external_register = self._find_external(r"grok_register\.register")
            if not auth_snap["running"]:
                external_auth = self._find_external(r"xai_enroller\.service")
            accounts = _count_lines(self.keys_dir / "accounts.txt")
            sessions = _count_lines(self.keys_dir / "auth-sessions.jsonl")
            oauth_files = _count_json_files(self.authenticated_dir)
            inventory = self._inventory_counts()
            pending_convert = max(0, sessions - oauth_files)
            return {
                "enabled": self.enabled,
                "project_root": str(self.project_root),
                "tokens_dir": str(self.authenticated_dir),
                "register": {
                    **register_snap,
                    "external": external_register,
                    "accounts_total": accounts,
                    "sessions_total": sessions,
                    "rate_hint": self._register_rate_hint(),
                },
                "auth": {
                    **auth_snap,
                    "external": external_auth,
                    "oauth_files": oauth_files,
                    "inventory": inventory,
                    "pending_convert_estimate": pending_convert,
                },
                "bridge": {
                    "note": (
                        "注册产出 SSO sessions -> auth 转 OAuth JSON -> Grok Tool 号池自动扫描。"
                        " 号池只吃 authenticated/*.json，不是 accounts.txt。"
                    ),
                    "accounts_total": accounts,
                    "sessions_total": sessions,
                    "oauth_files": oauth_files,
                    "pending_convert_estimate": pending_convert,
                },
            }
