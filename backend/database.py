from supabase import create_client, Client, ClientOptions
from backend.config import settings
import logging

logger = logging.getLogger(__name__)

# Global admin client instance
admin_client = None

def get_admin_client() -> Client:
    """Returns an administrative Supabase client instance."""
    global admin_client
    if admin_client is not None:
        return admin_client
        
    try:
        if settings.supabase_url:
            key = settings.supabase_service_role_key or settings.supabase_anon_key
            if key:
                admin_client = create_client(settings.supabase_url, key)
    except Exception as e:
        logger.warning(f"Failed to initialize Supabase admin client: {e}")
        
    return admin_client

# Initialize on module load
get_admin_client()

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
