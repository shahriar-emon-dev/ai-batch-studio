from supabase import create_client, Client, ClientOptions
from backend.config import settings
import logging

logger = logging.getLogger(__name__)

# Global admin client for operations that require bypassing RLS (like background workers updating status)
admin_client = None
try:
    if settings.supabase_url and settings.supabase_service_role_key:
        admin_client = create_client(settings.supabase_url, settings.supabase_service_role_key)
except Exception as e:
    logger.warning(f"Failed to initialize Supabase admin client: {e}")

def get_db_client(token: str = None) -> Client:
    """
    Returns a Supabase client.
    If a user token is provided, sets the auth header to act on behalf of the user.
    """
    if not settings.supabase_url or not settings.supabase_anon_key:
        raise ValueError("Supabase anon client not configured")
        
    if token:
        options = ClientOptions(headers={"Authorization": f"Bearer {token}"})
        return create_client(settings.supabase_url, settings.supabase_anon_key, options=options)
        
    return create_client(settings.supabase_url, settings.supabase_anon_key)
