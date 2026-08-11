import jwt
import base64
from cryptography.fernet import Fernet
from fastapi import Request, HTTPException, Security, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from backend.config import settings

security = HTTPBearer()

def get_cipher():
    if not settings.encryption_key:
        return None
    try:
        # Pad if necessary or ensure it's a valid 32 url-safe base64 string
        key = settings.encryption_key.encode('utf-8')
        if len(key) < 32:
            key = key.ljust(32, b'=')
        # Fernet requires url-safe base64 of 32 bytes
        encoded = base64.urlsafe_b64encode(key[:32])
        return Fernet(encoded)
    except Exception:
        return None

def encrypt_value(value: str) -> str:
    if not value: return value
    cipher = get_cipher()
    if not cipher: return value # Fallback if not configured (with warning already raised at startup)
    return cipher.encrypt(value.encode('utf-8')).decode('utf-8')

def decrypt_value(encrypted_value: str) -> str:
    if not encrypted_value: return encrypted_value
    cipher = get_cipher()
    if not cipher: return encrypted_value
    try:
        return cipher.decrypt(encrypted_value.encode('utf-8')).decode('utf-8')
    except Exception:
        return encrypted_value # Might be unencrypted legacy data

def get_token(credentials: HTTPAuthorizationCredentials = Security(security)) -> str:
    return credentials.credentials

def verify_token(token: str = Depends(get_token)) -> str:
    try:
        if settings.supabase_jwt_secret:
            payload = jwt.decode(
                token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                audience="authenticated"
            )
        else:
            payload = jwt.decode(token, options={"verify_signature": False})
            
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token: missing sub")
        return user_id
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")

async def get_current_user(user_id: str = Depends(verify_token)) -> str:
    return user_id
