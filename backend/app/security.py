from fastapi import Header, HTTPException
import httpx

from .config import settings


async def supabase_user(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Sign in with your verified email account.")
    if not settings.supabase_url or not settings.supabase_anon_key:
        raise HTTPException(503, "Supabase authentication is not configured.")
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(
                f"{settings.supabase_url.rstrip('/')}/auth/v1/user",
                headers={"apikey": settings.supabase_anon_key, "Authorization": authorization},
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(503, "Authentication service timed out. Try again shortly.") from exc
    except httpx.RequestError as exc:
        raise HTTPException(503, "Authentication service is temporarily unavailable.") from exc
    if response.status_code != 200:
        raise HTTPException(401, "Email account session is invalid or expired.")
    return response.json()
