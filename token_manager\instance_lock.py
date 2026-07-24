from __future__ import annotations

import ctypes
import hashlib
import os
from pathlib import Path
from typing import BinaryIO


_ERROR_ALREADY_EXISTS = 183


class InstanceLock:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self._windows_handle: int | None = None
        self._lock_file: BinaryIO | None = None

    @property
    def name(self) -> str:
        canonical = str(self.data_dir)
        if os.name == "nt":
            canonical = canonical.casefold()
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
        return f"GrokTool-{digest}"

    def acquire(self) -> bool:
        if self._windows_handle is not None or self._lock_file is not None:
            return True
        if os.name == "nt":
            return self._acquire_windows()
        return self._acquire_posix()

    def _acquire_windows(self) -> bool:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_bool
        ctypes.set_last_error(0)
        handle = kernel32.CreateMutexW(None, False, f"Local\\{self.name}")
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        self._windows_handle = int(handle)
        return True

    def _acquire_posix(self) -> bool:
        import fcntl

        self.data_dir.mkdir(parents=True, exist_ok=True)
        lock_file = open(self.data_dir / ".grok-tool.lock", "a+b")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_file.close()
            return False
        self._lock_file = lock_file
        return True

    def release(self) -> None:
        if self._windows_handle is not None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_bool
            kernel32.CloseHandle(self._windows_handle)
            self._windows_handle = None
        if self._lock_file is not None:
            import fcntl

            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            self._lock_file.close()
            self._lock_file = None

    def __enter__(self) -> "InstanceLock":
        if not self.acquire():
            raise RuntimeError("Grok Tool is already running for this data directory")
        return self

    def __exit__(self, *_args) -> None:
        self.release()
