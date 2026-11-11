"""Pydantic request/response models and Mongo document serialization helpers."""

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def serialize(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    """Convert a Mongo document to a JSON-friendly dict (ObjectId/datetime -> str)."""
    if doc is None:
        return None
    out: dict[str, Any] = {}
    for key, value in doc.items():
        if key == "_id":
            out["id"] = str(value)
        elif isinstance(value, datetime):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


# ---- Request bodies ----

class LoginRequest(BaseModel):
    # Provide a username to register / claim a name, or a token to restore an
    # existing session (e.g. after a page reload).
    username: str | None = None
    token: str | None = None


class CreateDMRequest(BaseModel):
    other_user_id: str


class CreateGroupRequest(BaseModel):
    name: str
    member_ids: list[str] = []
