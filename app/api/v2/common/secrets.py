from fastapi import HTTPException, Request

from app import settings

def validate_secret(request: Request, secret: str | None):
    if secret != settings.TRUSTED_SECRET:
        raise HTTPException(status_code=403, detail="Invaild secret.")
    if settings.LOCAL_ONLY and request.client.host not in ("127.0.0.1", "localhost"):
        raise HTTPException(status_code=403, detail="Invaild request.")