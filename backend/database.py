"""Supabase client factory.

Two kinds of client are used:

* `get_db_client(token)` — acts as the signed-in user. Row Level Security does
  the isolation, so a request can only ever touch that user's rows (§46).
* `get_admin_client()` — used by the background worker, which has no user
  request context. This requires the service-role key; with only the anon key
  RLS silently rejects every write, so we detect and report that explicitly
  rather than letting a job appear to run while persisting nothing.
"""

import logging
import threading
from collections import OrderedDict
from typing import Optional

from supabase import Client, ClientOptions, create_client

from backend.config import settings

logger = logging.getLogger(__name__)

admin_client: Optional[Client] = None
_admin_is_service_role = False

# Building a Supabase client sets up an HTTP session, which is wasteful to redo
# on every request of a polling UI. Cache per access token (the token *is* the
# identity, so entries can never leak across users) with a bounded size.
_USER_CLIENT_CACHE_SIZE = 64
_user_clients: "OrderedDict[str, Client]" = OrderedDict()
_cache_lock = threading.Lock()


def is_service_role_configured() -> bool:
    """True when the worker can write on behalf of users."""
    return bool(settings.supabase_service_role_key) and _admin_is_service_role


def get_admin_client() -> Optional[Client]:
    global admin_client, _admin_is_service_role
    if admin_client is not None:
        return admin_client

    if not settings.supabase_url:
        return None

    key = settings.supabase_service_role_key or settings.supabase_anon_key
    if not key:
        return None

    try:
        admin_client = create_client(settings.supabase_url, key)
        _admin_is_service_role = bool(settings.supabase_service_role_key)
        if not _admin_is_service_role:
            logger.warning(
                "SUPABASE_SERVICE_ROLE_KEY is not set. The generation worker cannot write "
                "generation_tasks, scenes or assets, so batch generation will not persist "
                "results. Add the service_role key from Supabase → Settings → API to .env."
            )
    except Exception as exc:
        logger.error("Failed to initialize the Supabase admin client: %s", exc)
        return None

    return admin_client


def get_db_client(token: Optional[str] = None) -> Client:
    """User-scoped client. RLS applies to every query."""
    if not settings.supabase_url or not settings.supabase_anon_key:
        raise ValueError("Supabase is not configured (SUPABASE_URL / SUPABASE_KEY)")

    if not token:
        return create_client(settings.supabase_url, settings.supabase_anon_key)

    with _cache_lock:
        client = _user_clients.get(token)
        if client is not None:
            _user_clients.move_to_end(token)
            return client

    options = ClientOptions(headers={"Authorization": f"Bearer {token}"})
    client = create_client(settings.supabase_url, settings.supabase_anon_key, options=options)

    with _cache_lock:
        _user_clients[token] = client
        while len(_user_clients) > _USER_CLIENT_CACHE_SIZE:
            _user_clients.popitem(last=False)
    return client


# Initialize eagerly so the warning above appears at startup, not mid-job.
get_admin_client()
