#!/usr/bin/env python3
"""Purge depleted / spending-limit dead tokens from Grok Tool pool."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from token_manager.pool import TokenPool
from token_manager.server import build_default_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Purge dead Grok Tool tokens")
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--tokens-dir", default=None)
    parser.add_argument("--keep-files", action="store_true", help="only remove from pool state")
    parser.add_argument("--id", default=None, help="optional token id")
    args = parser.parse_args(argv)

    config = build_default_config(ROOT)
    if args.data_dir:
        config.data_dir = str(Path(args.data_dir).resolve())
    if args.tokens_dir:
        config.tokens_dir = str(Path(args.tokens_dir).resolve())

    pool = TokenPool(config)
    result = pool.purge_dead_tokens(delete_files=not args.keep_files, token_id=args.id)
    # Never print secrets; balance summary already redacts master key.
    print(json.dumps({
        "removed_count": result["removed_count"],
        "delete_files": result["delete_files"],
        "removed_ids": [item.get("id") for item in result.get("removed") or []],
        "accounts_total": result["balance"].get("accounts_total"),
        "accounts_usable_now": result["balance"].get("accounts_usable_now"),
        "accounts_depleted": result["balance"].get("accounts_depleted"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
