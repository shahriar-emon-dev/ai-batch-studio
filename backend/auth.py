import jwt
from fastapi import Request, HTTPException, Security, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from backend.config import settings

security = HTTPBearer()

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
