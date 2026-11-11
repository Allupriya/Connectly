"""Auth and user endpoints plus the shared current-user dependency."""

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from ..config import get_logger
from ..db import users_collection
from ..models import LoginRequest, now_utc
from ..utils import hash_token, new_token, public_user

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["Auth & Users"])


def _extract_token(authorization: str | None, token: str | None) -> str | None:
    """Read the session token from a Bearer header or a `token` query param."""
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return token


async def authenticate(token: str | None) -> dict:
    """Resolve a session token to its user, or raise 401.

    The token itself is the secret credential; only its hash is stored, and a
    user's public id is never accepted as a credential. This means knowing
    someone's id (or username) does not grant access to their account.
    """
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = await users_collection().find_one({"token_hash": hash_token(token)})
    if not user:
        logger.warning("Auth failed: invalid session token.")
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return user


async def get_current_user(
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> dict:
    """Auth dependency: authenticate the caller by their secret session token."""
    try:
        return await authenticate(_extract_token(authorization, token))
    except HTTPException:
        raise
    except Exception:
        logger.exception("get_current_user failed.")
        raise HTTPException(status_code=500, detail="Failed to authenticate user")


@router.post("/login", summary="Register a username or restore a session")
async def login(body: LoginRequest):
    try:
        # Restore an existing session from a previously issued token.
        if body.token:
            user = await authenticate(body.token.strip())
            logger.info("Session restored for user '%s'.", user.get("username"))
            return public_user(user)

        username = (body.username or "").strip()
        if not username:
            logger.warning("Login rejected: empty username.")
            raise HTTPException(status_code=400, detail="Username is required")
        if len(username) > 40:
            raise HTTPException(status_code=400, detail="Username is too long")

        # Personal-use login: anyone who knows a username may log in as it. Each
        # login mints a fresh secret token (only its hash is stored) that the
        # client then uses to authenticate; the raw id is never a credential.
        token = new_token()
        users = users_collection()
        existing = await users.find_one({"username": username})
        if existing:
            await users.update_one(
                {"_id": existing["_id"]}, {"$set": {"token_hash": hash_token(token)}}
            )
            user = await users.find_one({"_id": existing["_id"]})
            logger.info("Login succeeded for existing user '%s'.", username)
        else:
            result = await users.insert_one(
                {
                    "username": username,
                    "created_at": now_utc(),
                    "token_hash": hash_token(token),
                }
            )
            user = await users.find_one({"_id": result.inserted_id})
            logger.info("Registered new user '%s'.", username)
        # The plaintext token is returned only here, in the login response.
        return {**public_user(user), "token": token}
    except HTTPException:
        raise
    except Exception:
        logger.exception("login failed for username=%r", body.username)
        raise HTTPException(status_code=500, detail="Login failed")


@router.get("/users", summary="List all other users")
async def list_users(current=Depends(get_current_user)):
    try:
        users = await users_collection().find().sort("username", 1).to_list(length=None)
        return [
            public_user(u) for u in users if str(u["_id"]) != str(current["_id"])
        ]
    except HTTPException:
        raise
    except Exception:
        logger.exception("list_users failed for user_id=%s", current.get("_id"))
        raise HTTPException(status_code=500, detail="Failed to list users")
