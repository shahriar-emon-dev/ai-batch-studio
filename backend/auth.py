"""Authentication, authorization and secret handling (proposal §8, §11, §46, §52).

Tokens are always verified. Three strategies, in order of preference:
  1. HS256 with the project's JWT secret (fastest, no network).
  2. Asymmetric verification against the project's JWKS endpoint.
  3. Remote verification against Supabase `/auth/v1/user`, cached briefly.

An unverified token is never trusted, so a forged `sub` cannot reach another
user's data.
"""

import base64
import logging
import time
from typing import Dict, Optional, Tuple

import httpx
import jwt
from cryptography.fernet import Fernet
from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.config import settings

logger = logging.getLogger(__name__)
security = HTTPBearer()

# token -> (user_id, expires_at) for remotely verified tokens
_remote_cache: Dict[str, Tuple[str, float]] = {}
_REMOTE_CACHE_TTL = 300
_jwks_client: Optional[jwt.PyJWKClient] = None
_jwks_failed = False


# ---------------------------------------------------------------------------
# API key encryption (§11)
# ---------------------------------------------------------------------------

def get_cipher() -> Optional[Fernet]:
    if not settings.encryption_key:
        return None
    try:
        key = settings.encryption_key.encode("utf-8")
        if len(key) < 32:
            key = key.ljust(32, b"=")
        return Fernet(base64.urlsafe_b64encode(key[:32]))
    except Exception as exc:
        logger.error("Encryption key is unusable: %s", exc)
        return None


def encrypt_value(value: str) -> str:
    if not value:
        return value
    cipher = get_cipher()
    if not cipher:
        # Refuse to persist a secret in the clear.
        raise HTTPException(
            status_code=500,
            detail="ENCRYPTION_KEY is not configured on the server; API keys cannot be stored securely.",
        )
    return cipher.encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_value(encrypted_value: str) -> str:
    if not encrypted_value:
        return encrypted_value
    cipher = get_cipher()
    if not cipher:
        return encrypted_value
    try:
        return cipher.decrypt(encrypted_value.encode("utf-8")).decode("utf-8")
    except Exception:
        # Value stored before encryption was configured.
        return encrypted_value


# ---------------------------------------------------------------------------
# Token verification (§8, §46)
# ---------------------------------------------------------------------------

def get_token(credentials: HTTPAuthorizationCredentials = Security(security)) -> str:
    return credentials.credentials


def _decode_with_secret(token: str) -> Optional[str]:
    if not settings.supabase_jwt_secret:
        return None
    try:
        payload = jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
        return payload.get("sub")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired. Please sign in again.")
    except jwt.PyJWTError as exc:
        logger.debug("HS256 verification failed: %s", exc)
        return None


def _decode_with_jwks(token: str) -> Optional[str]:
    """Supabase projects using asymmetric signing keys publish a JWKS."""
    global _jwks_client, _jwks_failed
    if _jwks_failed or not settings.supabase_url:
        return None

    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        return None
    if header.get("alg", "").startswith("HS"):
        return None

    try:
        if _jwks_client is None:
            _jwks_client = jwt.PyJWKClient(
                f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json",
                cache_keys=True,
            )
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=[header.get("alg")],
            audience="authenticated",
        )
        return payload.get("sub")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired. Please sign in again.")
    except Exception as exc:
        logger.info("JWKS verification unavailable (%s); falling back to remote verification", exc)
        _jwks_failed = True
        return None


async def _verify_remotely(token: str) -> Optional[str]:
    """Ask Supabase who this token belongs to. Authoritative, cached briefly."""
    cached = _remote_cache.get(token)
    now = time.time()
    if cached and cached[1] > now:
        return cached[0]

    if not settings.supabase_url or not settings.supabase_anon_key:
        return None

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{settings.supabase_url.rstrip('/')}/auth/v1/user",
                headers={"apikey": settings.supabase_anon_key, "Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError as exc:
        logger.error("Could not reach Supabase to verify the session: %s", exc)
        raise HTTPException(status_code=503, detail="Authentication service unavailable")

    if response.status_code != 200:
        return None

    user_id = (response.json() or {}).get("id")
    if not user_id:
        return None

    # Evict expired entries so the cache cannot grow without bound.
    for key, (_, expiry) in list(_remote_cache.items()):
        if expiry <= now:
            _remote_cache.pop(key, None)
    _remote_cache[token] = (user_id, now + _REMOTE_CACHE_TTL)
    return user_id


async def verify_token(token: str = Depends(get_token)) -> str:
    """Resolve the authenticated user id, or raise 401."""
    try:
        payload = jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid authentication token")

    if payload.get("exp") and payload["exp"] < time.time():
        raise HTTPException(status_code=401, detail="Session expired. Please sign in again.")

    user_id = _decode_with_secret(token) or _decode_with_jwks(token) or await _verify_remotely(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    return user_id


async def get_current_user(user_id: str = Depends(verify_token)) -> str:
    return user_id
