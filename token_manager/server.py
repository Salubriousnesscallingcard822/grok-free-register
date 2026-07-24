from __future__ import annotations

import json
import os
import hmac
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from .instance_lock import InstanceLock
from .models import ManagerConfig, iso
from .pipeline import PipelineController
from .pool import TokenPool


HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}

# CLIProxyAPI applyXAIChatHeaders for cli-chat-proxy. Without these the
# upstream reports Grok CLI version (none) and returns HTTP 426.
CLI_CHAT_PROXY_HOST = "cli-chat-proxy.grok.com"
CLI_CLIENT_VERSION = "0.2.93"
CLI_CHAT_IDENTITY_HEADERS = {
    "X-XAI-Token-Auth": "xai-grok-cli",
    "x-grok-client-version": CLI_CLIENT_VERSION,
    "User-Agent": f"xai-grok-workspace/{CLI_CLIENT_VERSION}",
}


def _is_cli_chat_proxy(url: str) -> bool:
    return CLI_CHAT_PROXY_HOST in (url or "").lower()


def _apply_cli_chat_identity(headers: dict[str, str], upstream_url: str) -> None:
    if not _is_cli_chat_proxy(upstream_url):
        return
    for key, value in CLI_CHAT_IDENTITY_HEADERS.items():
        headers[key] = value

def _runtime_base_dir() -> Path:
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _bundle_dir() -> Path:
    import sys
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


STATIC_DIR = _bundle_dir() / "static"
if not STATIC_DIR.exists():
    STATIC_DIR = Path(__file__).resolve().parent / "static"


class TokenManagerServer:
    def __init__(self, config: ManagerConfig, pool: TokenPool, pipeline: PipelineController | None = None):
        self.config = config
        self.pool = pool
        self.pipeline = pipeline
        self.httpd: ThreadingHTTPServer | None = None
        self._reload_stop = threading.Event()
        self._reload_thread: threading.Thread | None = None
        self._logs: list[dict[str, Any]] = []
        self._log_lock = threading.Lock()

    def add_log(self, level: str, message: str, **extra: Any) -> None:
        item = {"ts": iso(), "level": level, "message": message, **extra}
        with self._log_lock:
            self._logs.append(item)
            if len(self._logs) > 500:
                self._logs = self._logs[-500:]
        print(f"[grok-tool] {level}: {message}")

    def recent_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._log_lock:
            return list(self._logs[-limit:])

    def serve_forever(self) -> None:
        handler = self._build_handler()
        self.httpd = ThreadingHTTPServer((self.config.host, self.config.port), handler)
        self._reload_thread = threading.Thread(target=self._reload_loop, daemon=True)
        self._reload_thread.start()
        self.add_log("info", f"listening on http://{self.config.host}:{self.config.port}")
        self.add_log("info", f"master key ready ({self.pool.master_key[:12]}...)")
        self.add_log("info", f"tokens dir: {self.config.tokens_dir}")
        if self.pipeline is not None:
            self.add_log(
                "info",
                "pipeline control enabled" if self.pipeline.enabled else "pipeline control disabled (portable mode)",
            )
        print(f"[grok-tool] open UI: http://{self.config.host}:{self.config.port}/")
        print(f"[grok-tool] master key: {self.pool.balance_summary()['master_key_hint']}")
        print(f"[grok-tool] openai base: http://{self.config.host}:{self.config.port}/v1")
        try:
            self.httpd.serve_forever(poll_interval=0.5)
        finally:
            self._reload_stop.set()

    def _reload_loop(self) -> None:
        while not self._reload_stop.wait(self.config.auto_reload_seconds):
            try:
                imported = self.pool.reload_from_disk()
                if imported:
                    self.add_log("info", f"imported/updated {imported} token file(s)")
            except Exception as exc:
                self.add_log("error", f"reload error: {exc}")

    def bootstrap_payload(self) -> dict[str, Any]:
        balance = self.pool.balance_summary()
        pipeline = self.pipeline.status() if self.pipeline is not None else {"enabled": False}
        return {
            "app": "Grok Tool",
            "version": "0.2.0",
            "shell": "keyhub-style-desktop",
            "host": self.config.host,
            "port": self.config.port,
            "base_url": f"http://{self.config.host}:{self.config.port}/v1",
            "master_key": self.pool.master_key,
            "tokens_dir": self.config.tokens_dir,
            "data_dir": self.config.data_dir,
            "proxy_url": self.config.proxy_url,
            "free_units_per_account": self.config.free_units_per_account,
            "balance": balance,
            "pipeline": pipeline,
            "connection": {
                "codex": {
                    "base_url": f"http://{self.config.host}:{self.config.port}/v1",
                    "api_key": self.pool.master_key,
                },
                "keyhub_provider": {
                    "name": "Grok Tool Pool",
                    "channel": "grok",
                    "baseUrl": f"http://{self.config.host}:{self.config.port}/v1",
                    "apiKey": self.pool.master_key,
                    "headerName": "Authorization",
                    "headerPrefix": "Bearer ",
                    "priority": 10,
                    "weight": 1,
                    "timeoutMs": 30000,
                    "enabled": True,
                    "balancePath": "/balance",
                },
            },
            "endpoints": {
                "ui": f"http://{self.config.host}:{self.config.port}/",
                "balance": f"http://{self.config.host}:{self.config.port}/balance",
                "status": f"http://{self.config.host}:{self.config.port}/status",
                "proxy": f"http://{self.config.host}:{self.config.port}/v1",
                "pipeline": f"http://{self.config.host}:{self.config.port}/api/pipeline/status",
            },
            "logs": self.recent_logs(30),
        }

    def _build_handler(self):
        manager = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "GrokTool/0.2"

            def log_message(self, fmt: str, *args: Any) -> None:
                print(f"[grok-tool] {self.address_string()} {fmt % args}")

            def _client_ip(self) -> str:
                return self.client_address[0]

            def _is_local(self) -> bool:
                return self._client_ip() in {"127.0.0.1", "::1", "localhost"}

            def _read_body(self) -> bytes:
                length = int(self.headers.get("content-length") or 0)
                if length <= 0:
                    return b""
                return self.rfile.read(length)

            def _extract_key(self) -> str:
                header = self.headers.get("authorization") or self.headers.get("x-api-key") or ""
                if header.lower().startswith("bearer "):
                    return header[7:].strip()
                return header.strip()

            def _auth_ok(self) -> bool:
                token = self._extract_key()
                return bool(token) and hmac.compare_digest(
                    token, manager.pool.master_key
                )

            def _origin_ok(self) -> bool:
                return _origin_allowed(
                    self.headers.get("origin"),
                    manager.config.host,
                    manager.config.port,
                )

            def _local_or_auth(self) -> bool:
                return self._auth_ok() or (self._is_local() and self._origin_ok())

            def _send_cors(self) -> None:
                origin = self.headers.get("origin")
                if origin and self._origin_ok():
                    self.send_header("access-control-allow-origin", origin)
                    self.send_header("vary", "Origin")

            def _json(self, status: int, payload: dict[str, Any]) -> None:
                raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("content-type", "application/json; charset=utf-8")
                self.send_header("cache-control", "no-store")
                self._send_cors()
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _bytes(self, status: int, content: bytes, content_type: str, extra_headers: dict[str, str] | None = None) -> None:
                self.send_response(status)
                self.send_header("content-type", content_type)
                self.send_header("cache-control", "no-store")
                self.send_header("content-length", str(len(content)))
                if extra_headers:
                    for k, v in extra_headers.items():
                        self.send_header(k, v)
                self.end_headers()
                self.wfile.write(content)

            def do_OPTIONS(self) -> None:  # noqa: N802
                if not self._origin_ok():
                    self.send_response(403)
                    self.send_header("content-length", "0")
                    self.end_headers()
                    return
                self.send_response(204)
                self._send_cors()
                self.send_header("access-control-allow-headers", "authorization,content-type,x-api-key")
                self.send_header("access-control-allow-methods", "GET,POST,OPTIONS")
                self.end_headers()

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                path = parsed.path
                if path in {"/", "/index.html", "/app", "/dashboard"}:
                    self._serve_static("index.html", "text/html; charset=utf-8")
                    return
                if path.startswith("/static/"):
                    rel = path[len("/static/") :]
                    self._serve_static(rel)
                    return
                if path in {"/health", "/healthz"}:
                    self._json(200, {"ok": True, "ts": iso(), "app": "Grok Tool"})
                    return
                if path in {"/api/bootstrap", "/bootstrap"}:
                    if not self._local_or_auth():
                        self._json(401, {"error": {"message": "unauthorized", "type": "auth_error"}})
                        return
                    self._json(200, manager.bootstrap_payload())
                    return
                if path in {"/status", "/balance", "/v1/balance", "/api/balance", "/api/status"}:
                    if not self._local_or_auth():
                        self._json(401, {"error": {"message": "invalid master key", "type": "auth_error"}})
                        return
                    self._json(200, manager.pool.balance_summary())
                    return
                if path in {"/api/pipeline/status", "/pipeline/status"}:
                    if not self._local_or_auth():
                        self._json(401, {"error": {"message": "unauthorized", "type": "auth_error"}})
                        return
                    if manager.pipeline is None:
                        self._json(200, {"enabled": False})
                        return
                    self._json(200, manager.pipeline.status())
                    return
                if path in {"/api/logs", "/logs"}:
                    if not self._local_or_auth():
                        self._json(401, {"error": {"message": "unauthorized", "type": "auth_error"}})
                        return
                    qs = parse_qs(parsed.query)
                    limit = int((qs.get("limit") or ["100"])[0])
                    self._json(200, {"logs": manager.recent_logs(limit)})
                    return
                if path in {"/api/export/keyhub", "/export/keyhub"}:
                    if not self._local_or_auth():
                        self._json(401, {"error": {"message": "unauthorized", "type": "auth_error"}})
                        return
                    payload = manager.bootstrap_payload()["connection"]["keyhub_provider"]
                    self._json(200, payload)
                    return
                if path.startswith("/v1/"):
                    self._proxy(path if path != "/v1" else "/v1/models", method="GET")
                    return
                self._json(404, {"error": {"message": "not found", "type": "not_found"}})

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                path = parsed.path.rstrip("/") or "/"
                body = self._read_body()

                if path in {"/admin/reload", "/api/reload"}:
                    if not self._local_or_auth():
                        self._json(401, {"error": {"message": "unauthorized", "type": "auth_error"}})
                        return
                    count = manager.pool.reload_from_disk(force=True)
                    manager.add_log("info", f"manual reload updated={count}")
                    self._json(
                        200,
                        {
                            "imported_or_updated": count,
                            "balance": manager.pool.balance_summary(),
                            "pipeline": manager.pipeline.status() if manager.pipeline else {"enabled": False},
                        },
                    )
                    return

                if path in {"/admin/rotate-key", "/api/rotate-key"}:
                    if not self._local_or_auth():
                        self._json(401, {"error": {"message": "unauthorized", "type": "auth_error"}})
                        return
                    new_key = manager.pool.rotate_master_key()
                    manager.add_log("warn", "master key rotated")
                    self._json(200, {"master_key": new_key})
                    return

                if path in {"/admin/refresh", "/api/refresh"}:
                    if not self._local_or_auth():
                        self._json(401, {"error": {"message": "unauthorized", "type": "auth_error"}})
                        return
                    try:
                        data = json.loads(body.decode("utf-8") or "{}")
                    except ValueError:
                        data = {}
                    token_id = data.get("id")
                    results = []
                    targets = [manager.pool.get(token_id)] if token_id else manager.pool.list_tokens()
                    for token in targets:
                        if not token:
                            continue
                        try:
                            refreshed = manager.pool.refresh(token.id)
                            results.append({"id": refreshed.id, "ok": True, "expires_at": refreshed.expires_at})
                        except Exception as exc:
                            results.append({"id": token.id, "ok": False, "error": str(exc)})
                    manager.add_log("info", f"refresh finished count={len(results)}")
                    self._json(200, {"results": results, "balance": manager.pool.balance_summary()})
                    return

                if path in {"/admin/reset-depleted", "/api/reset-depleted"}:
                    if not self._local_or_auth():
                        self._json(401, {"error": {"message": "unauthorized", "type": "auth_error"}})
                        return
                    try:
                        data = json.loads(body.decode("utf-8") or "{}")
                    except ValueError:
                        data = {}
                    token_id = data.get("id")
                    changed = 0
                    for token in manager.pool.list_tokens():
                        if token_id and token.id != token_id:
                            continue
                        if token.depleted or token.free_units_used:
                            token.depleted = False
                            token.free_units_used = 0
                            token.healthy = True
                            token.last_error = None
                            token.rate_limited_until = None
                            changed += 1
                    manager.pool.save_state()
                    manager.add_log("info", f"reset depleted count={changed}")
                    self._json(200, {"reset": changed, "balance": manager.pool.balance_summary()})
                    return

                if path in {"/admin/purge-dead", "/api/purge-dead", "/admin/purge-depleted", "/api/purge-depleted"}:
                    if not self._local_or_auth():
                        self._json(401, {"error": {"message": "unauthorized", "type": "auth_error"}})
                        return
                    try:
                        data = json.loads(body.decode("utf-8") or "{}")
                    except ValueError:
                        data = {}
                    token_id = data.get("id")
                    delete_files = data.get("delete_files")
                    if delete_files is None:
                        delete_files = True
                    result = manager.pool.purge_dead_tokens(
                        delete_files=bool(delete_files),
                        token_id=token_id,
                    )
                    manager.add_log(
                        "warn",
                        f"purged dead tokens count={result['removed_count']} delete_files={bool(delete_files)}",
                    )
                    self._json(200, result)
                    return

                if path.startswith("/api/pipeline/") or path.startswith("/pipeline/"):
                    if not self._local_or_auth():
                        self._json(401, {"error": {"message": "unauthorized", "type": "auth_error"}})
                        return
                    if manager.pipeline is None:
                        self._json(400, {"ok": False, "error": "pipeline unavailable"})
                        return
                    action = path.rsplit("/", 1)[-1]
                    if action == "start-register":
                        result = manager.pipeline.start_register()
                    elif action == "stop-register":
                        result = manager.pipeline.stop_register()
                    elif action == "start-auth":
                        result = manager.pipeline.start_auth()
                    elif action == "stop-auth":
                        result = manager.pipeline.stop_auth()
                    elif action == "start-all":
                        result = manager.pipeline.start_all()
                    elif action == "stop-all":
                        result = manager.pipeline.stop_all()
                    else:
                        self._json(404, {"error": {"message": f"unknown pipeline action: {action}"}})
                        return
                    manager.add_log("info", f"pipeline action={action} result={result.get('ok', result)}")
                    imported = manager.pool.reload_from_disk(force=True)
                    self._json(
                        200,
                        {
                            "action": action,
                            "result": result,
                            "imported_or_updated": imported,
                            "pipeline": manager.pipeline.status(),
                            "balance": manager.pool.balance_summary(),
                        },
                    )
                    return

                if path.startswith("/v1"):
                    path_norm = path if path.startswith("/v1/") else "/v1/models"
                    # re-inject body for proxy by temporarily stashing
                    self._proxy(path_norm, method="POST", body=body)
                    return

                self._json(404, {"error": {"message": "not found", "type": "not_found"}})

            def _serve_static(self, rel: str, content_type: str | None = None) -> None:
                rel = rel.lstrip("/").replace("\\", "/")
                if ".." in rel.split("/"):
                    self._json(400, {"error": {"message": "bad path"}})
                    return
                target = (STATIC_DIR / rel).resolve()
                if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.exists() or not target.is_file():
                    # fallback index for SPA-ish root assets
                    if rel in {"", "index.html"}:
                        target = STATIC_DIR / "index.html"
                    else:
                        self._json(404, {"error": {"message": f"static missing: {rel}"}})
                        return
                data = target.read_bytes()
                ctype = content_type or mimetypes.guess_type(str(target))[0] or "application/octet-stream"
                self._bytes(200, data, ctype)

            def _proxy(self, path: str, method: str, body: bytes | None = None) -> None:
                if not self._auth_ok():
                    self._json(401, {"error": {"message": "invalid master key", "type": "auth_error"}})
                    return
                if body is None:
                    body = self._read_body() if method != "GET" else b""
                query = urlparse(self.path).query
                path_only = path.split("?", 1)[0]
                # OpenAI-compatible clients probe /v1/models often. Serving a local
                # catalog avoids burning free OAuth accounts on a weak health signal.
                if method == "GET" and path_only.rstrip("/").endswith("/models"):
                    payload = {
                        "object": "list",
                        "data": [
                            {"id": "grok-3", "object": "model", "owned_by": "xai"},
                            {"id": "grok-3-mini", "object": "model", "owned_by": "xai"},
                            {"id": "grok-2", "object": "model", "owned_by": "xai"},
                            {"id": "grok-2-mini", "object": "model", "owned_by": "xai"},
                        ],
                    }
                    self._json(200, payload)
                    return
                last_error = "no_token"
                last_error_type = "pool_exhausted"
                # Prefer a usable account first; if the selected account is only
                # transiently broken, rotate once more instead of false-emptying.
                attempts = 2
                for attempt in range(attempts):
                    try:
                        token = manager.pool.acquire()
                        token = manager.pool.ensure_fresh(token)
                    except Exception as exc:
                        last_error = str(exc)
                        err_l = last_error.lower()
                        if "depleted" in err_l or "spending-limit" in err_l or "quota" in err_l:
                            last_error_type = "upstream_quota_exhausted"
                        elif "cooling down" in err_l:
                            last_error_type = "pool_cooling_down"
                        elif "empty" in err_l or "no enabled" in err_l or "no callable" in err_l:
                            last_error_type = "pool_empty"
                        else:
                            last_error_type = "pool_exhausted"
                        break
                    try:
                        upstream_base = manager.pool.upstream_base_for(token).rstrip("/")
                    except ValueError as exc:
                        last_error = str(exc)
                        last_error_type = "upstream_config_error"
                        manager.pool.mark_result(
                            token.id,
                            ok=False,
                            error="unsafe_upstream_configuration",
                            endpoint=path_only,
                        )
                        break
                    if upstream_base.endswith("/v1") and path.startswith("/v1/"):
                        upstream = upstream_base + path[3:]
                    else:
                        upstream = upstream_base.rstrip("/") + path
                    if query:
                        upstream += "?" + query
                    headers: dict[str, str] = {}
                    for key, value in self.headers.items():
                        lk = key.lower()
                        if lk in HOP_BY_HOP or lk in {"authorization", "x-api-key", "x-keyhub-key"}:
                            continue
                        headers[key] = value
                    headers["Authorization"] = f"Bearer {token.access_token}"
                    _apply_cli_chat_identity(headers, upstream)
                    try:
                        with manager._client() as client:
                            response = client.request(method, upstream, headers=headers, content=body)
                        content = response.content
                        usage = _extract_usage(content, response.headers.get("content-type", ""))
                        text_preview = content.decode("utf-8", errors="replace")[:300]
                        ok = 200 <= response.status_code < 400
                        manager.pool.mark_result(
                            token.id,
                            ok=ok,
                            status_code=response.status_code,
                            error=None if ok else text_preview,
                            usage=usage,
                            rate_limit_seconds=60 if response.status_code == 429 else None,
                            endpoint=path_only,
                        )
                        manager.add_log(
                            "info" if ok else "warn",
                            f"{method} {path} -> {response.status_code} via {token.email or token.id}",
                            status=response.status_code,
                            account=token.email or token.id,
                        )
                        if not ok and attempt + 1 < attempts:
                            # Rotate once on transient/auth/quota failures so a single
                            # dead account does not fail a still-usable pool.
                            continue
                        if not ok and manager.pool._is_quota_error(text_preview, response.status_code):
                            last_error = text_preview
                            last_error_type = "upstream_quota_exhausted"
                            # If there may still be other accounts, keep rotating once.
                            if attempt + 1 < attempts:
                                continue
                            self._json(
                                402 if response.status_code == 402 else 503,
                                {
                                    "error": {
                                        "message": f"upstream quota exhausted: {text_preview}",
                                        "type": last_error_type,
                                    }
                                },
                            )
                            return
                        extra = {
                            "x-grok-tool-account": token.email or token.id,
                            "x-grok-tool-attempts": str(attempt + 1),
                        }
                        self.send_response(response.status_code)
                        skip = HOP_BY_HOP | {
                            "content-length",
                            "content-encoding",
                            "transfer-encoding",
                            "content-md5",
                        }
                        for key, value in response.headers.items():
                            if key.lower() in skip:
                                continue
                            self.send_header(key, value)
                        for key, value in extra.items():
                            self.send_header(key, value)
                        self.send_header("content-length", str(len(content)))
                        self.end_headers()
                        self.wfile.write(content)
                        return
                    except Exception as exc:
                        last_error = str(exc)
                        last_error_type = "upstream_transport_error"
                        manager.pool.mark_result(
                            token.id,
                            ok=False,
                            error=str(exc),
                            endpoint=path_only,
                        )
                        manager.add_log("error", f"proxy error via {token.id}: {exc}")
                        continue
                self._json(
                    503,
                    {
                        "error": {
                            "message": f"upstream unavailable: {last_error}",
                            "type": last_error_type,
                        }
                    },
                )

        return Handler

    def _client(self) -> httpx.Client:
        kwargs: dict[str, Any] = {
            "timeout": self.config.request_timeout_seconds,
            "follow_redirects": False,
            "trust_env": bool(self.config.proxy_url),
        }
        proxy = self.config.proxy_url
        if proxy:
            try:
                return httpx.Client(proxy=proxy, **kwargs)
            except TypeError:
                return httpx.Client(proxies=proxy, **kwargs)
        return httpx.Client(**kwargs)


def _extract_usage(content: bytes, content_type: str) -> dict[str, int] | None:
    if "application/json" not in (content_type or "") and not content[:1] == b"{":
        return None
    try:
        data = json.loads(content.decode("utf-8"))
    except Exception:
        return None
    usage = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(usage, dict):
        return None
    return {
        "prompt_tokens": int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
    }


def _origin_allowed(origin: str | None, host: str, port: int) -> bool:
    """Allow native clients and the local dashboard, never arbitrary websites."""
    if not origin:
        return True
    try:
        parsed = urlparse(origin)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
        return False
    expected_port = port or (443 if parsed.scheme == "https" else 80)
    try:
        actual_port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return False
    return actual_port == expected_port


def build_default_config(project_root: Path | None = None) -> ManagerConfig:
    import os
    import sys

    # Portable layout when frozen:
    #   GrokTool.exe
    #   data/          <- master key + pool state
    #   tokens/        <- drop OAuth json here
    root = (project_root or _runtime_base_dir()).resolve()
    if getattr(sys, "frozen", False):
        data_dir = root / "data"
        tokens_dir = root / "tokens"
    else:
        portable = (os.environ.get("GROK_TOOL_PORTABLE") or "").strip().lower() in {"1", "true", "yes"}
        if portable:
            data_dir = root / "portable" / "data"
            tokens_dir = root / "portable" / "tokens"
        else:
            auth_local = root / "auth-local"
            data_dir = auth_local / "token-manager"
            tokens_dir = auth_local / "authenticated"

    if os.environ.get("TOKEN_MANAGER_DATA_DIR"):
        data_dir = _resolve_runtime_path(os.environ["TOKEN_MANAGER_DATA_DIR"], root)
    if os.environ.get("TOKEN_MANAGER_TOKENS_DIR"):
        tokens_dir = _resolve_runtime_path(os.environ["TOKEN_MANAGER_TOKENS_DIR"], root)

    data_dir.mkdir(parents=True, exist_ok=True)
    tokens_dir.mkdir(parents=True, exist_ok=True)

    proxy = (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("HTTP_PROXY")
        or ""
    )
    if "TOKEN_MANAGER_PROXY" in os.environ:
        proxy = os.environ.get("TOKEN_MANAGER_PROXY") or None
    port = int(os.environ.get("TOKEN_MANAGER_PORT") or 8787)
    host = os.environ.get("TOKEN_MANAGER_HOST") or "127.0.0.1"
    master = os.environ.get("TOKEN_MANAGER_MASTER_KEY") or ""
    free_units = int(os.environ.get("TOKEN_MANAGER_FREE_UNITS") or 100)
    return ManagerConfig(
        host=host,
        port=port,
        master_key=master,
        data_dir=str(data_dir),
        tokens_dir=str(tokens_dir),
        proxy_url=proxy,
        free_units_per_account=free_units,
    )


def _resolve_runtime_path(value: str, root: Path | None = None) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (root or _runtime_base_dir()) / path
    return path.resolve()



def _want_desktop_window() -> bool:
    raw = (os.environ.get("GROK_TOOL_DESKTOP") or "1").strip().lower()
    if raw in {"0", "false", "no"}:
        return False
    return True


def _open_desktop_window(url: str) -> bool:
    try:
        import webview  # type: ignore
    except Exception:
        return False

    def _runner() -> None:
        webview.create_window(
            "Grok Tool",
            url,
            width=1280,
            height=860,
            min_size=(980, 680),
            background_color="#0b1020",
        )
        webview.start()

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    return True


def main(argv: list[str] | None = None) -> int:
    import argparse
    import time
    import webbrowser

    parser = argparse.ArgumentParser(description="Grok Tool - KeyHub-style unified token manager")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--master-key", default=None)
    parser.add_argument("--tokens-dir", default=None)
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--proxy", default=None)
    parser.add_argument("--free-units", type=int, default=None)
    parser.add_argument("--desktop", action="store_true", help="force native desktop window")
    parser.add_argument("--no-desktop", action="store_true", help="disable native desktop window")
    parser.add_argument("--browser", action="store_true", help="open system browser")
    args = parser.parse_args(argv)

    config = build_default_config()
    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port
    if args.master_key:
        config.master_key = args.master_key
    if args.tokens_dir:
        config.tokens_dir = str(_resolve_runtime_path(args.tokens_dir))
    if args.data_dir:
        config.data_dir = str(_resolve_runtime_path(args.data_dir))
    if args.proxy is not None:
        config.proxy_url = args.proxy or None
    if args.free_units is not None:
        config.free_units_per_account = args.free_units

    instance_lock = InstanceLock(config.data_dir)
    if not instance_lock.acquire():
        print("[grok-tool] another instance already uses this data directory")
        return 2
    try:
        pool = TokenPool(config)
        imported = pool.reload_from_disk(force=True)
        print(f"[grok-tool] loaded tokens: {len(pool.list_tokens())} (scan updated={imported})")
        print(f"[grok-tool] tokens dir: {config.tokens_dir}")
        print(f"[grok-tool] data dir  : {config.data_dir}")
        pipeline = PipelineController(_runtime_base_dir())
        server = TokenManagerServer(config, pool, pipeline=pipeline)

        url = f"http://{config.host}:{config.port}/"
        use_desktop = _want_desktop_window()
        if args.desktop:
            use_desktop = True
        if args.no_desktop:
            use_desktop = False
        open_browser = args.browser or (
            (os.environ.get("GROK_TOOL_OPEN_BROWSER") or "").strip().lower() in {"1", "true", "yes"}
        )
        # Default portable/desktop UX: native window if available, otherwise browser.
        if not args.browser and (os.environ.get("GROK_TOOL_OPEN_BROWSER") or "").strip() == "":
            open_browser = not use_desktop

        def _ui_boot() -> None:
            time.sleep(0.8)
            if use_desktop and _open_desktop_window(url):
                print(f"[grok-tool] desktop window: {url}")
                return
            if open_browser or use_desktop:
                try:
                    webbrowser.open(url)
                    print(f"[grok-tool] browser: {url}")
                except Exception:
                    pass

        threading.Thread(target=_ui_boot, daemon=True).start()
        server.serve_forever()
        return 0
    finally:
        instance_lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
