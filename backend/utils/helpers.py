"""Shared helpers used across routers: id parsing, tokens and document shaping."""

import hashlib
import secrets

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException

from ..config import get_logger
from ..db import users_collection
from ..services import count_unread, get_last_message, manager

logger = get_logger(__name__)


def new_token() -> str:
    """Generate a fresh, unguessable session token."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Hash a token for storage so a DB leak never exposes usable credentials."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def to_object_id(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        logger.warning("Rejected invalid ObjectId: %r", value)
        raise HTTPException(status_code=400, detail=f"Invalid id: {value}")


def public_user(user: dict) -> dict:
    return {
        "id": str(user["_id"]),
        "username": user["username"],
        "online": manager.is_online(str(user["_id"])),
    }


async def chat_with_details(chat: dict, viewer_id: str) -> dict:
    """Enrich a chat document with member info and its last message."""
    try:
        member_ids = [to_object_id(m) for m in chat["members"]]
        members = await users_collection().find(
            {"_id": {"$in": member_ids}}
        ).to_list(length=None)
        members_public = [public_user(m) for m in members]

        if chat["type"] == "dm":
            others = [m for m in members_public if m["id"] != viewer_id]
            display_name = others[0]["username"] if others else "Direct message"
        else:
            display_name = chat.get("name", "Group")

        last_message = await get_last_message(str(chat["_id"]))

        last_read = chat.get("last_read", {})
        unread_count = await count_unread(
            str(chat["_id"]), viewer_id, last_read.get(viewer_id)
        )

        return {
            "id": str(chat["_id"]),
            "type": chat["type"],
            "name": display_name,
            "members": members_public,
            "last_message": last_message,
            "last_read": last_read,
            "unread_count": unread_count,
        }
    except HTTPException:
        raise
    except Exception:
        logger.exception(
            "chat_with_details failed for chat_id=%s.", chat.get("_id")
        )
        raise
