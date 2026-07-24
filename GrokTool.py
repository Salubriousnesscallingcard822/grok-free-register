"""Portable entrypoint for Grok Tool (PyInstaller-friendly desktop shell)."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _prepare_env() -> None:
    os.environ.setdefault("GROK_TOOL_PORTABLE", "1")
    os.environ.setdefault("GROK_TOOL_DESKTOP", "1")
    # Prefer native desktop window; browser is fallback only.
    os.environ.setdefault("GROK_TOOL_OPEN_BROWSER", "0")
    os.environ.setdefault("TOKEN_MANAGER_HOST", "127.0.0.1")
    os.environ.setdefault("TOKEN_MANAGER_PORT", "8787")
    # Proxy is optional; set HTTP_PROXY/HTTPS_PROXY yourself if needed.
def main() -> int:
    _prepare_env()
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    from token_manager.server import main as server_main
    return server_main()


if __name__ == "__main__":
    raise SystemExit(main())
