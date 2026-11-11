"""Chat endpoints: list chats, create DMs/groups and fetch message history."""

from fastapi import APIRouter, Depends, HTTPException, Query

from ..config import get_logger
from ..db import chats_collection, users_collection
from ..models import CreateDMRequest, CreateGroupRequest, now_utc
from ..services import get_chat_messages, manager
from ..utils import chat_with_details, to_object_id
from .users import get_current_user

logger = get_logger(__name__)

router = APIRouter(prefix="/api/chats", tags=["Chats"])


@router.get("", summary="List my chats")
async def list_chats(current=Depends(get_current_user)):
    try:
        me = str(current["_id"])
        chats = await chats_collection().find({"members": me}).to_list(length=None)
        detailed = [await chat_with_details(c, me) for c in chats]
        # Most recently active chats first.
        detailed.sort(
            key=lambda c: (c["last_message"] or {}).get("created_at", ""),
            reverse=True,
        )
        return detailed
    except HTTPException:
        raise
    except Exception:
        logger.exception("list_chats failed for user_id=%s", current.get("_id"))
        raise HTTPException(status_code=500, detail="Failed to list chats")


@router.post("/dm", summary="Start (or reuse) a direct message")
async def create_dm(body: CreateDMRequest, current=Depends(get_current_user)):
    try:
        me = str(current["_id"])
        other = body.other_user_id

        if other == me:
            logger.warning("create_dm rejected: user %s tried to DM themselves.", me)
            raise HTTPException(status_code=400, detail="Cannot start a DM with yourself")

        other_user = await users_collection().find_one({"_id": to_object_id(other)})
        if not other_user:
            logger.warning("create_dm: target user %s not found.", other)
            raise HTTPException(status_code=404, detail="User not found")

        chats = chats_collection()
        # Reuse an existing DM between these two users if one exists.
        existing = await chats.find_one(
            {"type": "dm", "members": {"$all": [me, other], "$size": 2}}
        )
        if existing:
            logger.info("create_dm: reusing existing DM between %s and %s.", me, other)
            return await chat_with_details(existing, me)

        result = await chats.insert_one(
            {
                "type": "dm",
                "members": [me, other],
                "created_by": me,
                "created_at": now_utc(),
            }
        )
        chat = await chats.find_one({"_id": result.inserted_id})
        logger.info("create_dm: new DM %s created between %s and %s.", result.inserted_id, me, other)
        return await chat_with_details(chat, me)
    except HTTPException:
        raise
    except Exception:
        logger.exception(
            "create_dm failed (user_id=%s, other_user_id=%s)",
            current.get("_id"),
            getattr(body, "other_user_id", None),
        )
        raise HTTPException(status_code=500, detail="Failed to create direct message")


@router.post("/group", summary="Create a group chat")
async def create_group(body: CreateGroupRequest, current=Depends(get_current_user)):
    try:
        me = str(current["_id"])
        name = body.name.strip()
        if not name:
            logger.warning("create_group rejected: empty group name from user %s.", me)
            raise HTTPException(status_code=400, detail="Group name is required")

        members = set(body.member_ids) | {me}
        # Validate all member ids exist.
        object_ids = [to_object_id(m) for m in members]
        found = await users_collection().count_documents({"_id": {"$in": object_ids}})
        if found != len(members):
            logger.warning(
                "create_group: some members not found (expected %s, found %s).",
                len(members),
                found,
            )
            raise HTTPException(status_code=404, detail="One or more members not found")

        chats = chats_collection()
        result = await chats.insert_one(
            {
                "type": "group",
                "name": name,
                "members": list(members),
                "created_by": me,
                "created_at": now_utc(),
            }
        )
        chat = await chats.find_one({"_id": result.inserted_id})
        detailed = await chat_with_details(chat, me)
        logger.info("create_group: group '%s' (%s) created by %s.", name, result.inserted_id, me)

        # Let other online members know about the new group immediately.
        try:
            await manager.send_to_users(
                [m for m in members if m != me],
                {"type": "chat_created", "chat": detailed},
            )
        except Exception:
            # Broadcasting is best-effort; don't fail the request if it errors.
            logger.exception("create_group: failed to broadcast chat_created event.")
        return detailed
    except HTTPException:
        raise
    except Exception:
        logger.exception("create_group failed for user_id=%s", current.get("_id"))
        raise HTTPException(status_code=500, detail="Failed to create group")


@router.get("/{chat_id}/messages", summary="Get chat message history")
async def get_messages(
    chat_id: str,
    current=Depends(get_current_user),
    limit: int = Query(100, le=500),
):
    try:
        chat = await chats_collection().find_one({"_id": to_object_id(chat_id)})
        if not chat or str(current["_id"]) not in chat["members"]:
            logger.warning(
                "get_messages denied: user %s is not a member of chat %s.",
                current.get("_id"),
                chat_id,
            )
            raise HTTPException(status_code=403, detail="Not a member of this chat")

        return await get_chat_messages(chat_id, limit=limit)
    except HTTPException:
        raise
    except Exception:
        logger.exception("get_messages failed for chat_id=%s", chat_id)
        raise HTTPException(status_code=500, detail="Failed to load messages")
