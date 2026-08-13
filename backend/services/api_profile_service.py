"""Google AI API profile manager (proposal §10, §11, §13).

Holds the decrypted keys for the duration of a job only, rotates between
legitimately configured profiles, and parks a profile when the provider
reports a quota or rate-limit condition. It never bypasses provider limits —
it only moves on to the next credential the user configured.
"""

import asyncio
import datetime
import logging
from typing import Any, Dict, List, Optional

from backend.auth import decrypt_value
from backend.config import settings
from backend.database import get_admin_client
from backend.services.google_ai_service import (
    AuthError,
    ProviderError,
    QuotaExceededError,
    RateLimitException,
)

logger = logging.getLogger(__name__)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _parse_ts(value: Any) -> Optional[datetime.datetime]:
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def mask_key(raw_key: str) -> str:
    """Frontend-safe representation — last 4 characters only (§11, §47)."""
    if not raw_key:
        return ""
    if len(raw_key) <= 8:
        return "•" * len(raw_key)
    return f"{'•' * 12}{raw_key[-4:]}"


class NoAvailableProfileError(Exception):
    """Every configured profile is exhausted or unusable.

    `cause` carries the last provider error so the pause reason shown to the
    user says *why* (invalid key, quota, rate limit) instead of just "paused".
    """

    def __init__(self, message: str, cause: Optional[Exception] = None):
        super().__init__(message)
        self.cause = cause


class ApiProfilePool:
    """Round-robin pool over a user's active Google AI profiles."""

    def __init__(self, profiles: List[Dict[str, Any]]):
        self._profiles = profiles
        self._index = 0
        self._lock = asyncio.Lock()
        self.last_error: Optional[Exception] = None

    @property
    def size(self) -> int:
        return len(self._profiles)

    def _is_available(self, profile: Dict[str, Any]) -> bool:
        until = profile.get("unavailable_until_dt")
        return not until or until <= _now()

    def available_count(self) -> int:
        return sum(1 for p in self._profiles if self._is_available(p))

    def next_retry_at(self) -> Optional[datetime.datetime]:
        times = [p.get("unavailable_until_dt") for p in self._profiles if p.get("unavailable_until_dt")]
        return min(times) if times else None

    async def acquire(self) -> Dict[str, Any]:
        """Return the next usable profile, or raise NoAvailableProfileError."""
        async with self._lock:
            for _ in range(len(self._profiles)):
                profile = self._profiles[self._index % len(self._profiles)]
                self._index += 1
                if self._is_available(profile):
                    return profile

        reason = f" Last provider response: {self.last_error}" if self.last_error else ""
        raise NoAvailableProfileError(
            f"All {len(self._profiles)} configured Google AI profile(s) are unavailable.{reason}",
            cause=self.last_error,
        )

    async def report_success(self, profile: Dict[str, Any]) -> None:
        profile["success_count"] = profile.get("success_count", 0) + 1
        profile["request_count"] = profile.get("request_count", 0) + 1
        profile["unavailable_until_dt"] = None
        _persist_health(
            profile,
            {
                "request_count": profile["request_count"],
                "success_count": profile["success_count"],
                "last_success_at": _now().isoformat(),
                "last_error": None,
                "unavailable_until": None,
                "connection_status": "active",
            },
        )

    async def report_failure(self, profile: Dict[str, Any], error: Exception) -> None:
        """Park the profile when the provider says it is out of capacity."""
        profile["failure_count"] = profile.get("failure_count", 0) + 1
        profile["request_count"] = profile.get("request_count", 0) + 1
        self.last_error = error

        updates: Dict[str, Any] = {
            "request_count": profile["request_count"],
            "failure_count": profile["failure_count"],
            "last_error": str(error)[:500],
            "last_error_at": _now().isoformat(),
        }

        cooldown = None
        if isinstance(error, QuotaExceededError):
            cooldown = settings.quota_cooldown_seconds
            updates["connection_status"] = "quota_exceeded"
        elif isinstance(error, RateLimitException):
            cooldown = settings.rate_limit_cooldown_seconds
            updates["connection_status"] = "rate_limited"
        elif isinstance(error, AuthError):
            # A bad key never recovers on its own — take it out of rotation.
            cooldown = 24 * 60 * 60
            updates["connection_status"] = "invalid"
            updates["test_result"] = "INVALID_API_KEY"

        if cooldown:
            until = _now() + datetime.timedelta(seconds=cooldown)
            profile["unavailable_until_dt"] = until
            updates["unavailable_until"] = until.isoformat()
            logger.warning(
                "API profile %s parked for %ss (%s)",
                profile.get("profile_name"),
                cooldown,
                type(error).__name__,
            )

        _persist_health(profile, updates)


def _persist_health(profile: Dict[str, Any], updates: Dict[str, Any]) -> None:
    """Best-effort health write — must never break generation."""
    client = get_admin_client()
    if not client or not profile.get("id"):
        return
    try:
        client.table("api_profiles").update(updates).eq("id", profile["id"]).execute()
    except Exception as exc:  # pragma: no cover - telemetry only
        logger.debug("Could not persist profile health: %s", exc)


def load_profile_pool(client, user_id: Optional[str] = None) -> ApiProfilePool:
    """Build a pool from the user's active Google profiles.

    `client` should be a user-scoped client (RLS) when called from a request,
    in which case `user_id` is redundant but harmless.
    """
    query = (
        client.table("api_profiles")
        .select("id, profile_name, encrypted_credentials, unavailable_until, request_count, success_count, failure_count, priority")
        .eq("provider", "google")
        .eq("is_active", True)
    )
    if user_id:
        query = query.eq("user_id", user_id)

    rows = query.execute().data or []
    profiles: List[Dict[str, Any]] = []

    for row in rows:
        encrypted = row.get("encrypted_credentials")
        if not encrypted:
            continue
        raw_key = decrypt_value(encrypted)
        if not raw_key:
            continue
        profiles.append(
            {
                "id": row["id"],
                "profile_name": row.get("profile_name") or f"Profile {row['id']}",
                "api_key": raw_key,
                "request_count": row.get("request_count") or 0,
                "success_count": row.get("success_count") or 0,
                "failure_count": row.get("failure_count") or 0,
                "priority": row.get("priority") or 0,
                "unavailable_until_dt": _parse_ts(row.get("unavailable_until")),
            }
        )

    profiles.sort(key=lambda p: (-p["priority"], p["id"]))
    return ApiProfilePool(profiles)


async def call_with_rotation(pool: ApiProfilePool, operation, *args, **kwargs):
    """Run `operation(api_key, *args)` retrying across profiles and backoff.

    Returns (result, profile_used). Raises the last provider error when every
    attempt is exhausted. Non-retryable errors (bad prompt, unsupported model)
    propagate immediately.
    """
    attempts = max(1, settings.retry_max_attempts)
    last_error: Optional[Exception] = None

    for attempt in range(attempts):
        profile = await pool.acquire()
        try:
            result = await operation(profile["api_key"], *args, **kwargs)
            await pool.report_success(profile)
            return result, profile
        except (QuotaExceededError, RateLimitException, AuthError) as exc:
            last_error = exc
            await pool.report_failure(profile, exc)
            if pool.available_count() == 0 and attempt == attempts - 1:
                raise
            continue
        except ProviderError as exc:
            last_error = exc
            await pool.report_failure(profile, exc)
            if not getattr(exc, "retryable", False):
                raise
            if attempt < attempts - 1:
                delay = min(settings.retry_base_delay * (2 ** attempt), settings.retry_max_delay)
                logger.info("Retrying after %.1fs (%s)", delay, type(exc).__name__)
                await asyncio.sleep(delay)

    if last_error:
        raise last_error
    raise ProviderError("Operation failed with no recorded error")
