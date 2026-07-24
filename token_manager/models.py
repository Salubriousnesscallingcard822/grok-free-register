from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    value = dt or utcnow()
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


@dataclass
class TokenRecord:
    id: str
    access_token: str = ""
    refresh_token: str = ""
    token_endpoint: str = "https://auth.x.ai/oauth2/token"
    base_url: str = "https://cli-chat-proxy.grok.com/v1"
    subject: str | None = None
    email: str | None = None
    expires_at: str | None = None
    last_refresh: str | None = None
    source_file: str | None = None
    enabled: bool = True
    healthy: bool = True
    depleted: bool = False
    rate_limited_until: str | None = None
    last_error: str | None = None
    last_used_at: str | None = None
    created_at: str = field(default_factory=iso)
    updated_at: str = field(default_factory=iso)
    request_count: int = 0
    success_count: int = 0
    error_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    free_units_total: int = 100
    free_units_used: int = 0
    weight: int = 1

    def to_dict(self, *, include_secrets: bool = True) -> dict[str, Any]:
        payload = asdict(self)
        if not include_secrets:
            payload.pop("access_token", None)
            payload.pop("refresh_token", None)
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TokenRecord":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        payload = {k: v for k, v in data.items() if k in known}
        return cls(**payload)

    @property
    def free_units_remaining(self) -> int:
        return max(0, int(self.free_units_total) - int(self.free_units_used))

    def is_expired(self, skew_seconds: int = 120) -> bool:
        expires = parse_iso(self.expires_at)
        if expires is None:
            return False
        return expires.timestamp() <= utcnow().timestamp() + skew_seconds

    def is_rate_limited(self) -> bool:
        until = parse_iso(self.rate_limited_until)
        if until is None:
            return False
        return until > utcnow()

    def usable(self) -> bool:
        return (
            self.enabled
            and self.healthy
            and not self.depleted
            and not self.is_rate_limited()
            and bool(self.access_token)
            and bool(self.refresh_token)
        )


@dataclass
class ManagerConfig:
    host: str = "127.0.0.1"
    port: int = 8787
    master_key: str = ""
    data_dir: str = ""
    tokens_dir: str = ""
    upstream_base: str = "https://cli-chat-proxy.grok.com/v1"
    token_endpoint: str = "https://auth.x.ai/oauth2/token"
    client_id: str = "b1a00492-073a-47ea-816f-4c329264a828"
    proxy_url: str | None = "http://127.0.0.1:7897"
    free_units_per_account: int = 100
    refresh_skew_seconds: int = 300
    request_timeout_seconds: float = 120.0
    auto_reload_seconds: int = 15

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
