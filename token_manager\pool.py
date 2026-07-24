from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from .models import ManagerConfig, TokenRecord, iso, utcnow
from .secret_store import protect_for_current_user, unprotect_for_current_user


class TokenPool:
    """Disk-backed pool of Grok OAuth credentials with RR selection and refresh."""

    def __init__(self, config: ManagerConfig):
        self.config = config
        self.data_dir = Path(config.data_dir)
        self.tokens_dir = Path(config.tokens_dir)
        self.state_path = self.data_dir / "pool-state.json"
        self.legacy_master_path = self.data_dir / "master-key.txt"
        self.master_path = (
            self.data_dir / "master-key.dpapi"
            if os.name == "nt"
            else self.legacy_master_path
        )
        self._lock = threading.RLock()
        self._tokens: dict[str, TokenRecord] = {}
        self._rr_index = 0
        self._source_versions: dict[str, tuple[int, int]] = {}
        self._refresh_locks: dict[str, threading.Lock] = {}
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.tokens_dir.mkdir(parents=True, exist_ok=True)
        self._secure_directory(self.data_dir)
        self._secure_directory(self.tokens_dir)
        self._ensure_writable_directory(self.data_dir)
        self._ensure_writable_directory(self.tokens_dir)
        self.master_key = self._load_or_create_master_key(config.master_key)
        self._load_state()
        self.reload_from_disk(force=True)

    @staticmethod
    def _ensure_writable_directory(path: Path) -> None:
        try:
            with tempfile.NamedTemporaryFile(dir=path, prefix=".grok-tool-write-test-"):
                pass
        except OSError as exc:
            raise RuntimeError(f"directory is not writable: {path}") from exc

    @staticmethod
    def _secure_directory(path: Path) -> None:
        try:
            os.chmod(path, 0o700)
        except OSError as exc:
            raise RuntimeError(f"failed to secure directory: {path}") from exc
        if os.name != "nt":
            return
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            identity = subprocess.run(
                ["whoami"],
                check=True,
                capture_output=True,
                text=True,
                creationflags=creation_flags,
            ).stdout.strip()
            if identity:
                subprocess.run(
                    [
                        "icacls",
                        str(path),
                        "/inheritance:r",
                        "/grant:r",
                        f"{identity}:(OI)(CI)F",
                        "/T",
                        "/C",
                        "/Q",
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creation_flags,
                )
                subprocess.run(
                    [
                        "icacls",
                        str(path),
                        "/grant",
                        f"{identity}:F",
                        "/T",
                        "/C",
                        "/Q",
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creation_flags,
                )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"failed to secure directory ACL: {path}") from exc

    @staticmethod
    def _atomic_write_bytes(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.chmod(temp_path, 0o600)
            except OSError:
                pass
            os.replace(temp_path, path)
            if os.name != "nt":
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    @classmethod
    def _atomic_write_text(cls, path: Path, value: str) -> None:
        cls._atomic_write_bytes(path, value.encode("utf-8"))

    @classmethod
    def _atomic_write_json(cls, path: Path, payload: dict[str, Any]) -> None:
        cls._atomic_write_text(
            path,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )

    @staticmethod
    def _normalize_https_url(value: str, label: str) -> str:
        try:
            parsed = urlparse(str(value).strip())
            port = parsed.port
        except ValueError as exc:
            raise ValueError(f"invalid {label}") from exc
        host = parsed.hostname
        if (
            parsed.scheme.lower() != "https"
            or not host
            or parsed.username
            or parsed.password
            or parsed.params
            or parsed.query
            or parsed.fragment
            or port not in {None, 443}
        ):
            raise ValueError(f"invalid {label}")
        try:
            host.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError(f"invalid {label}") from exc
        path = parsed.path.rstrip("/")
        return urlunparse(("https", host.lower(), path, "", "", ""))

    def _service_url(self, candidate: str, configured: str, label: str) -> str:
        expected = self._normalize_https_url(configured, label)
        actual = self._normalize_https_url(candidate or configured, label)
        if actual != expected:
            raise ValueError(f"credential file cannot override {label}")
        return expected

    def upstream_base_for(self, token: TokenRecord) -> str:
        return self._service_url(
            token.base_url,
            self.config.upstream_base,
            "upstream base URL",
        )

    def token_endpoint_for(self, token: TokenRecord) -> str:
        return self._service_url(
            token.token_endpoint,
            self.config.token_endpoint,
            "token endpoint",
        )

    def _load_or_create_master_key(self, configured: str) -> str:
        if configured:
            key = configured.strip()
            self._persist_master_key(key)
            return key
        if self.master_path.exists():
            existing = self._read_master_key()
            if existing:
                return existing
        if os.name == "nt" and self.legacy_master_path.exists():
            existing = self.legacy_master_path.read_text(encoding="utf-8").strip()
            if existing:
                self._persist_master_key(existing)
                return existing
        key = "gk_master_" + secrets.token_urlsafe(24)
        self._persist_master_key(key)
        return key

    def _read_master_key(self) -> str:
        if os.name == "nt":
            try:
                plaintext = unprotect_for_current_user(self.master_path.read_bytes())
                return plaintext.decode("utf-8").strip()
            except (OSError, UnicodeError, RuntimeError) as exc:
                raise RuntimeError(
                    "failed to load encrypted master key; it may belong to another "
                    "Windows user or machine"
                ) from exc
        return self.master_path.read_text(encoding="utf-8").strip()

    def _persist_master_key(self, key: str) -> None:
        if os.name == "nt":
            encrypted = protect_for_current_user(key.encode("utf-8"))
            self._atomic_write_bytes(self.master_path, encrypted)
            if self.legacy_master_path.exists():
                self.legacy_master_path.unlink()
            return
        self._atomic_write_text(self.master_path, key + "\n")

    def rotate_master_key(self) -> str:
        with self._lock:
            new_key = "gk_master_" + secrets.token_urlsafe(24)
            self._persist_master_key(new_key)
            self.master_key = new_key
            self.save_state()
            return new_key

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

    def _load_state(self) -> None:
        if not self.state_path.exists():
            return
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        integrity = raw.pop("integrity", None)
        if integrity:
            expected = self._state_integrity(raw)
            if not hmac.compare_digest(str(integrity), expected):
                return
        tokens = raw.get("tokens") or {}
        for token_id, payload in tokens.items():
            try:
                record = TokenRecord.from_dict(payload)
                record.access_token = ""
                record.refresh_token = ""
                record.enabled = False
                record.healthy = False
                self._tokens[token_id] = record
                if record.source_file:
                    source = Path(record.source_file)
                    version = self._source_version(source)
                    if version is not None:
                        self._source_versions[str(source.resolve())] = version
            except Exception:
                continue
        self._rr_index = int(raw.get("rr_index") or 0)

    def _state_integrity(self, payload: dict[str, Any]) -> str:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hmac.new(
            self.master_key.encode("utf-8"),
            canonical,
            hashlib.sha256,
        ).hexdigest()

    def save_state(self) -> None:
        with self._lock:
            payload = {
                "updated_at": iso(),
                "rr_index": self._rr_index,
                "tokens": {
                    tid: rec.to_dict(include_secrets=False)
                    for tid, rec in self._tokens.items()
                },
            }
            payload["integrity"] = self._state_integrity(payload)
            self._atomic_write_json(self.state_path, payload)

    def _disable_source_records(
        self,
        source: str,
        reason: str,
        *,
        keep_id: str | None = None,
    ) -> int:
        changed = 0
        for record in self._tokens.values():
            if record.source_file != source or record.id == keep_id:
                continue
            if record.enabled or record.last_error != reason:
                record.enabled = False
                record.healthy = False
                record.last_error = reason
                record.updated_at = iso()
                changed += 1
        return changed

    def reload_from_disk(self, force: bool = False) -> int:
        """Import new OAuth json files from tokens_dir."""
        imported = 0
        sources_changed = False
        files = sorted(self.tokens_dir.glob("*.json"))
        active_sources = {str(path.resolve()) for path in files}
        with self._lock:
            for path in files:
                resolved = str(path.resolve())
                version = self._source_version(path)
                if version is None:
                    continue
                if not force and self._source_versions.get(resolved) == version:
                    continue
                sources_changed = True
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    imported += self._disable_source_records(
                        resolved,
                        "source_file_invalid",
                    )
                    self._source_versions[resolved] = version
                    continue
                if not isinstance(data, dict):
                    imported += self._disable_source_records(
                        resolved,
                        "source_file_invalid",
                    )
                    self._source_versions[resolved] = version
                    continue
                access = data.get("access_token")
                refresh = data.get("refresh_token")
                if not access or not refresh:
                    imported += self._disable_source_records(
                        resolved,
                        "source_credentials_missing",
                    )
                    self._source_versions[resolved] = version
                    continue
                subject = data.get("sub") or data.get("subject") or self._subject_from_jwt(access)
                token_id = subject or path.stem
                try:
                    token_endpoint = self._service_url(
                        str(data.get("token_endpoint") or self.config.token_endpoint),
                        self.config.token_endpoint,
                        "token endpoint",
                    )
                    base_url = self._service_url(
                        str(data.get("base_url") or self.config.upstream_base),
                        self.config.upstream_base,
                        "upstream base URL",
                    )
                except ValueError:
                    imported += self._disable_source_records(
                        resolved,
                        "source_endpoint_rejected",
                    )
                    self._source_versions[resolved] = version
                    continue
                imported += self._disable_source_records(
                    resolved,
                    "source_identity_replaced",
                    keep_id=token_id,
                )
                existing = self._tokens.get(token_id)
                credentials_changed = bool(
                    existing
                    and (
                        existing.access_token != str(access)
                        or existing.refresh_token != str(refresh)
                    )
                )
                record = existing or TokenRecord(
                    id=token_id,
                    access_token=str(access),
                    refresh_token=str(refresh),
                    free_units_total=self.config.free_units_per_account,
                )
                record.access_token = str(access)
                record.refresh_token = str(refresh)
                record.token_endpoint = token_endpoint
                record.base_url = base_url
                record.subject = subject
                record.email = data.get("email") or record.email or self._email_from_id_token(data.get("id_token"))
                record.expires_at = data.get("expired") or data.get("expires_at") or record.expires_at
                record.last_refresh = data.get("last_refresh") or record.last_refresh
                record.source_file = resolved
                record.enabled = True
                if credentials_changed:
                    record.healthy = True
                    record.last_error = None
                if record.depleted and record.free_units_remaining > 0:
                    record.depleted = False
                record.updated_at = iso()
                if not existing:
                    record.created_at = iso()
                    record.free_units_total = self.config.free_units_per_account
                self._tokens[token_id] = record
                self._source_versions[resolved] = version
                imported += 1
            for record in self._tokens.values():
                if not record.source_file:
                    continue
                source = str(Path(record.source_file).resolve())
                if source in active_sources:
                    continue
                try:
                    managed = Path(source).is_relative_to(self.tokens_dir.resolve())
                except ValueError:
                    managed = False
                if managed and record.enabled:
                    record.enabled = False
                    record.healthy = False
                    record.last_error = "source_file_missing"
                    record.updated_at = iso()
                    imported += 1
            for source in set(self._source_versions) - active_sources:
                self._source_versions.pop(source, None)
            if sources_changed:
                self._secure_directory(self.tokens_dir)
            if imported:
                self.save_state()
        return imported

    @staticmethod
    def _source_version(path: Path) -> tuple[int, int] | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return stat.st_mtime_ns, stat.st_size

    @staticmethod
    def _subject_from_jwt(token: str) -> str | None:
        try:
            import base64
            import json as _json

            parts = token.split(".")
            if len(parts) < 2:
                return None
            payload = parts[1] + "=" * (-len(parts[1]) % 4)
            claims = _json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
            for key in ("sub", "principal_id"):
                value = claims.get(key)
                if isinstance(value, str) and value:
                    return value
        except Exception:
            return None
        return None

    @staticmethod
    def _email_from_id_token(token: str | None) -> str | None:
        if not token:
            return None
        try:
            import base64
            import json as _json

            parts = token.split(".")
            if len(parts) < 2:
                return None
            payload = parts[1] + "=" * (-len(parts[1]) % 4)
            claims = _json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
            email = claims.get("email")
            return email if isinstance(email, str) else None
        except Exception:
            return None

    def list_tokens(self) -> list[TokenRecord]:
        with self._lock:
            return list(self._tokens.values())

    def get(self, token_id: str) -> TokenRecord | None:
        with self._lock:
            return self._tokens.get(token_id)

    def balance_summary(self) -> dict[str, Any]:
        with self._lock:
            tokens = list(self._tokens.values())
        total = len(tokens)
        usable = [t for t in tokens if t.usable() and not t.is_expired()]
        # include expired-but-refreshable as potentially usable
        refreshable = [t for t in tokens if t.enabled and not t.depleted and t.refresh_token]
        free_total = sum(t.free_units_total for t in tokens)
        free_used = sum(t.free_units_used for t in tokens)
        free_remaining = sum(t.free_units_remaining for t in tokens)
        prompt = sum(t.prompt_tokens for t in tokens)
        completion = sum(t.completion_tokens for t in tokens)
        requests = sum(t.request_count for t in tokens)
        successes = sum(t.success_count for t in tokens)
        failures = sum(t.error_count for t in tokens)
        return {
            "master_key_hint": self._mask(self.master_key),
            "accounts_total": total,
            "accounts_usable_now": len(usable),
            "accounts_refreshable": len(refreshable),
            "accounts_depleted": sum(1 for t in tokens if t.depleted),
            "accounts_unhealthy": sum(1 for t in tokens if not t.healthy),
            "free_units_total": free_total,
            "free_units_used": free_used,
            "free_units_remaining": free_remaining,
            "balance_display": f"{free_remaining}/{free_total} free-units",
            "requests_total": requests,
            "success_total": successes,
            "failed_total": failures,
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
            "note": (
                "free-units 是本地号池预算（每账号默认 100）。"
                " xAI 免费 OAuth 账号通常不暴露真实美元余额；"
                "耗尽会体现为 429/resource-exhausted。"
            ),
            "tokens": [
                {
                    "id": t.id,
                    "email": t.email,
                    "usable": t.usable() and not t.is_expired(),
                    "expired": t.is_expired(),
                    "depleted": t.depleted,
                    "healthy": t.healthy,
                    "free_units_remaining": t.free_units_remaining,
                    "free_units_total": t.free_units_total,
                    "request_count": t.request_count,
                    "success_count": t.success_count,
                    "error_count": t.error_count,
                    "prompt_tokens": t.prompt_tokens,
                    "completion_tokens": t.completion_tokens,
                    "last_error": t.last_error,
                    "expires_at": t.expires_at,
                    "source_file": t.source_file,
                }
                for t in sorted(tokens, key=lambda x: x.created_at)
            ],
        }

    @staticmethod
    def _mask(value: str) -> str:
        if not value:
            return ""
        if len(value) <= 12:
            return value[:3] + "..."
        return value[:8] + "..." + value[-4:]

    def acquire(self) -> TokenRecord:
        with self._lock:
            tokens = list(self._tokens.values())
            if not tokens:
                raise RuntimeError("token pool is empty: no credentials imported")
            candidates = [
                token
                for token in tokens
                if token.enabled and not token.depleted and token.refresh_token
            ]
            if not candidates:
                depleted = sum(1 for token in tokens if token.depleted)
                disabled = sum(1 for token in tokens if not token.enabled)
                if depleted:
                    raise RuntimeError(
                        "all tokens depleted (upstream spending-limit/quota); "
                        "purge dead tokens or import fresh credentials"
                    )
                if disabled == len(tokens):
                    raise RuntimeError("token pool has no enabled credentials")
                raise RuntimeError("token pool is empty: no callable credentials")
            available = [token for token in candidates if not token.is_rate_limited()]
            live = [
                token
                for token in available
                if token.usable() and not token.is_expired()
            ]
            refreshable = [
                token
                for token in available
                if token.refresh_token
                and (
                    not token.healthy
                    or not token.access_token
                    or token.is_expired(self.config.refresh_skew_seconds)
                )
            ]
            # Prefer currently usable tokens; fall back to refreshable ones.
            # Keep refreshable candidates even when healthy=False so a brief
            # network blip cannot empty the round-robin pool.
            pool = live or refreshable or available
            if not pool:
                if any(token.is_rate_limited() for token in candidates):
                    raise RuntimeError("all tokens are cooling down")
                raise RuntimeError("no usable or refreshable tokens")
            selected = pool[self._rr_index % len(pool)]
            self._rr_index = (self._rr_index + 1) % max(1, len(pool))
            selected.last_used_at = iso()
            selected.request_count += 1
            selected.updated_at = iso()
            self.save_state()
            return selected

    def ensure_fresh(self, token: TokenRecord) -> TokenRecord:
        if (
            not token.healthy
            or token.is_expired(self.config.refresh_skew_seconds)
            or not token.access_token
        ):
            return self.refresh(token.id)
        return token

    def refresh(self, token_id: str) -> TokenRecord:
        with self._lock:
            token = self._tokens.get(token_id)
            if token is None:
                raise KeyError(token_id)
            if not token.enabled:
                raise RuntimeError("token is disabled")
            if token.depleted:
                raise RuntimeError("token is depleted")
            requested_refresh = token.refresh_token
            refresh_lock = self._refresh_locks.setdefault(token_id, threading.Lock())
        with refresh_lock:
            with self._lock:
                token = self._tokens[token_id]
                if (
                    token.refresh_token != requested_refresh
                    and token.access_token
                    and token.healthy
                ):
                    return token
                refresh_token = token.refresh_token
                if not refresh_token:
                    raise RuntimeError("token has no refresh credential")
                endpoint = self.token_endpoint_for(token)
            with self._client() as client:
                response = client.post(
                    endpoint,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "client_id": self.config.client_id,
                    },
                )
            body = (
                response.json()
                if response.headers.get("content-type", "").startswith(
                    "application/json"
                )
                else {}
            )
            if response.status_code // 100 != 2:
                with self._lock:
                    token = self._tokens[token_id]
                    if token.refresh_token == refresh_token:
                        token.healthy = False
                        token.last_error = f"refresh_failed:{response.status_code}"
                        token.updated_at = iso()
                        self.save_state()
                raise RuntimeError(f"refresh failed: {response.status_code}")
            access = body.get("access_token")
            if not access:
                with self._lock:
                    token = self._tokens[token_id]
                    if token.refresh_token == refresh_token:
                        token.healthy = False
                        token.last_error = "refresh_response_missing_access_token"
                        token.updated_at = iso()
                        self.save_state()
                raise RuntimeError("refresh response missing access_token")
            expires_in = int(body.get("expires_in") or 21600)
            new_refresh = str(body.get("refresh_token") or refresh_token)
            expires_at = iso(datetime_from_now(expires_in))
            refreshed_at = iso()
            with self._lock:
                token = self._tokens[token_id]
                if token.refresh_token != refresh_token:
                    return token
                if not token.source_file or not Path(token.source_file).exists():
                    token.enabled = False
                    token.healthy = False
                    token.last_error = "source_file_missing"
                    token.updated_at = iso()
                    self.save_state()
                    raise RuntimeError("credential source file is missing")
                source_path = Path(token.source_file)
                try:
                    original = json.loads(source_path.read_text(encoding="utf-8"))
                    if not isinstance(original, dict):
                        raise ValueError("credential source is not an object")
                    original["access_token"] = str(access)
                    original["refresh_token"] = new_refresh
                    original["expired"] = expires_at
                    original["last_refresh"] = refreshed_at
                    original["expires_in"] = expires_in
                    self._atomic_write_json(source_path, original)
                except (OSError, ValueError) as exc:
                    token.healthy = False
                    token.last_error = "source_update_failed"
                    token.updated_at = iso()
                    self.save_state()
                    raise RuntimeError("failed to persist refreshed credentials") from exc
                token.access_token = str(access)
                token.refresh_token = new_refresh
                token.expires_at = expires_at
                token.last_refresh = refreshed_at
                token.healthy = True
                token.last_error = None
                token.updated_at = iso()
                version = self._source_version(source_path)
                if version is not None:
                    self._source_versions[str(source_path.resolve())] = version
                self.save_state()
                return token

    @staticmethod
    def _is_models_endpoint(endpoint: str | None) -> bool:
        if not endpoint:
            return False
        path = endpoint.split("?", 1)[0].rstrip("/")
        return path.endswith("/models") or path == "models"

    @staticmethod
    def _error_text(error: str | None, status_code: int | None = None) -> str:
        return (error or (f"http_{status_code}" if status_code is not None else "")).lower()

    @classmethod
    def _is_quota_error(cls, error: str | None, status_code: int | None = None) -> bool:
        err_l = cls._error_text(error, status_code)
        markers = (
            "resource-exhausted",
            "spending-limit",
            "run out of credits",
            "personal-team-blocked",
            "insufficient_quota",
            "payment required",
        )
        if any(marker in err_l for marker in markers):
            return True
        return status_code == 402

    @classmethod
    def _is_auth_error(cls, error: str | None, status_code: int | None = None) -> bool:
        err_l = cls._error_text(error, status_code)
        markers = (
            "invalid_grant",
            "invalid_token",
            "unauthorized",
            "token has been expired",
            "token expired",
            "authentication",
        )
        if any(marker in err_l for marker in markers):
            return True
        return status_code == 401

    @classmethod
    def _is_transient_error(cls, error: str | None, status_code: int | None = None) -> bool:
        err_l = cls._error_text(error, status_code)
        markers = (
            "winerror",
            "10054",
            "10053",
            "10060",
            "timed out",
            "timeout",
            "temporarily unavailable",
            "connection reset",
            "connection aborted",
            "connection refused",
            "remote host",
            "broken pipe",
            "eof occurred",
            "network is unreachable",
            "proxy error",
            "ssl",
        )
        if any(marker in err_l for marker in markers):
            return True
        return status_code in {408, 502, 503, 504}

    @classmethod
    def is_dead_token(cls, token: TokenRecord) -> bool:
        if token.depleted:
            return True
        if cls._is_quota_error(token.last_error):
            return True
        if cls._is_auth_error(token.last_error):
            return True
        err_l = cls._error_text(token.last_error)
        if any(
            marker in err_l
            for marker in (
                "refresh_failed",
                "invalid_grant",
                "invalid_token",
                "token has been expired",
                "token expired",
                "revoked",
            )
        ):
            return True
        # Access token already expired and cannot be refreshed.
        if token.is_expired() and not (token.refresh_token or "").strip():
            return True
        # Expired + unhealthy means refresh already failed or credential is unusable.
        if token.is_expired() and not token.healthy:
            return True
        if not token.enabled and (token.last_error or "") in {
            "source_file_missing",
            "source_credentials_missing",
            "source_file_invalid",
            "source_endpoint_rejected",
            "source_identity_replaced",
        }:
            return True
        return False

    def mark_result(
        self,
        token_id: str,
        *,
        ok: bool,
        status_code: int | None = None,
        error: str | None = None,
        usage: dict[str, int] | None = None,
        rate_limit_seconds: int | None = None,
        endpoint: str | None = None,
        count_usage: bool | None = None,
    ) -> None:
        models_probe = self._is_models_endpoint(endpoint)
        if count_usage is None:
            count_usage = not models_probe
        with self._lock:
            token = self._tokens.get(token_id)
            if token is None:
                return
            if ok:
                token.success_count += 1
                token.healthy = True
                token.last_error = None
                token.rate_limited_until = None
                if count_usage:
                    token.free_units_used = min(
                        token.free_units_total,
                        token.free_units_used + 1,
                    )
                    if token.free_units_remaining <= 0:
                        token.depleted = True
            else:
                token.error_count += 1
                token.last_error = error or f"http_{status_code}"
                quota_exhausted = self._is_quota_error(error, status_code)
                auth_failed = self._is_auth_error(error, status_code)
                transient = self._is_transient_error(error, status_code)
                if models_probe:
                    # /v1/models is a weak health signal for free OAuth accounts.
                    # Never burn local free-units, cool down, or permanently kill the pool.
                    if auth_failed:
                        token.healthy = False
                    else:
                        # Keep credential immediately callable for chat probes.
                        token.healthy = True
                        token.rate_limited_until = None
                elif quota_exhausted:
                    token.depleted = True
                    token.healthy = False
                    token.free_units_used = token.free_units_total
                elif auth_failed:
                    token.healthy = False
                elif status_code == 429:
                    token.healthy = True
                    token.rate_limited_until = iso(
                        datetime_from_now(rate_limit_seconds or 60)
                    )
                elif transient:
                    token.healthy = True
                    token.rate_limited_until = iso(
                        datetime_from_now(rate_limit_seconds or 15)
                    )
                elif status_code == 403:
                    # Non-quota 403: short cool-down, not permanent death.
                    token.healthy = True
                    token.rate_limited_until = iso(
                        datetime_from_now(rate_limit_seconds or 30)
                    )
            if usage:
                token.prompt_tokens += int(usage.get("prompt_tokens") or 0)
                token.completion_tokens += int(usage.get("completion_tokens") or 0)
                token.total_tokens = token.prompt_tokens + token.completion_tokens
            token.updated_at = iso()
            self.save_state()

    def purge_dead_tokens(
        self,
        *,
        delete_files: bool = True,
        token_id: str | None = None,
    ) -> dict[str, Any]:
        """Remove depleted / spending-limit dead accounts from pool and optional source files."""
        removed: list[dict[str, Any]] = []
        with self._lock:
            targets = [
                token
                for token in self._tokens.values()
                if (token_id is None or token.id == token_id) and self.is_dead_token(token)
            ]
            for token in targets:
                source_deleted = False
                source_path = Path(token.source_file) if token.source_file else None
                if delete_files and source_path is not None:
                    try:
                        managed = source_path.resolve().is_relative_to(self.tokens_dir.resolve())
                    except ValueError:
                        managed = False
                    if managed and source_path.exists():
                        try:
                            source_path.unlink()
                            source_deleted = True
                        except OSError:
                            source_deleted = False
                    if source_path is not None:
                        self._source_versions.pop(str(source_path.resolve()), None)
                removed.append(
                    {
                        "id": token.id,
                        "email": token.email,
                        "depleted": token.depleted,
                        "last_error": token.last_error,
                        "source_file": token.source_file,
                        "source_deleted": source_deleted,
                    }
                )
                self._tokens.pop(token.id, None)
                self._refresh_locks.pop(token.id, None)
            if removed:
                if self._tokens:
                    self._rr_index %= len(self._tokens)
                else:
                    self._rr_index = 0
                self.save_state()
        return {
            "removed_count": len(removed),
            "removed": removed,
            "delete_files": bool(delete_files),
            "balance": self.balance_summary(),
        }


def datetime_from_now(seconds: int):
    from datetime import timedelta

    return utcnow() + timedelta(seconds=int(seconds))
