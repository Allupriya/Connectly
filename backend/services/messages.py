"""Message storage: one document per (chat, sender, date).

Instead of a document per message, every user gets a single document per day
per chat. Its ``messages`` field is a dict whose *keys are the message order*
(``"0"``, ``"1"`` ...) and whose *values are the message context*. New messages
just update that dict in place, so we never create a document per message.

Example document in the messages collection::

    {
        "chat_id": "...",
        "sender_id": "...",
        "sender_username": "alice",
        "date": "2026-07-28",
        "count": 2,
        "messages": {
            "0": {"msg_type": "text", "content": "hi", ...},
            "1": {"msg_type": "text", "content": "how are you?", ...}
        }
    }

So two users chatting on the same day produce exactly two documents.
"""

from datetime import datetime

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ReturnDocument

from ..config import get_logger
from ..db import messages_collection
from ..models import now_utc

logger = get_logger(__name__)


def _date_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _as_iso(value) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _flatten(doc_id, chat_id, sender_id, sender_username, order, message: dict) -> dict:
    """Turn a stored (order -> context) entry into the flat message the API/UI use."""
    return {
        "id": f"{doc_id}:{order}",
        "chat_id": chat_id,
        "sender_id": sender_id,
        "sender_username": sender_username,
        "order": order,
        "msg_type": message.get("msg_type", "text"),
        "content": message.get("content", ""),
        "file_url": message.get("file_url"),
        "file_name": message.get("file_name"),
        "created_at": _as_iso(message.get("created_at")),
        "edited_at": _as_iso(message.get("edited_at")),
        "deleted": bool(message.get("deleted")),
        "reply_to": message.get("reply_to"),
    }


async def add_message(
    *,
    chat_id: str,
    sender_id: str,
    sender_username: str,
    msg_type: str,
    content: str,
    file_url: str | None = None,
    file_name: str | None = None,
    reply_to: dict | None = None,
) -> dict:
    """Append a message to this user's document for today, updating it in place."""
    try:
        col = messages_collection()
        created = now_utc()
        key = {"chat_id": chat_id, "sender_id": sender_id, "date": _date_key(created)}

        # Reserve the next order slot atomically (creates the doc on first message).
        doc = await col.find_one_and_update(
            key,
            {"$inc": {"count": 1}, "$set": {"sender_username": sender_username}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        order = doc["count"] - 1

        message = {
            "msg_type": msg_type,
            "content": content,
            "file_url": file_url,
            "file_name": file_name,
            "created_at": created,
            "reply_to": reply_to,
        }
        await col.update_one(key, {"$set": {f"messages.{order}": message}})

        return _flatten(doc["_id"], chat_id, sender_id, sender_username, order, message)
    except Exception:
        logger.exception(
            "add_message failed (chat_id=%s, sender_id=%s, msg_type=%s).",
            chat_id,
            sender_id,
            msg_type,
        )
        raise


def _flatten_doc(doc: dict) -> list[dict]:
    out = []
    for order_str, message in (doc.get("messages") or {}).items():
        out.append(
            _flatten(
                doc["_id"],
                doc["chat_id"],
                doc["sender_id"],
                doc.get("sender_username"),
                int(order_str),
                message,
            )
        )
    return out


async def get_chat_messages(chat_id: str, limit: int = 100) -> list[dict]:
    """Return this chat's messages (from every user/day doc) oldest-first."""
    try:
        docs = await messages_collection().find({"chat_id": chat_id}).to_list(length=None)
        flat = [m for doc in docs for m in _flatten_doc(doc)]
        flat.sort(key=lambda m: m["created_at"] or "")
        if limit:
            flat = flat[-limit:]
        return flat
    except Exception:
        logger.exception("get_chat_messages failed for chat_id=%s.", chat_id)
        raise


async def count_unread(chat_id: str, viewer_id: str, last_read_iso: str | None) -> int:
    """Count messages in this chat the viewer hasn't read yet.

    A message is unread when it was sent by someone else and created strictly
    after the viewer's ``last_read`` timestamp for this chat. When the viewer
    has never opened the chat (no ``last_read``), every message from others
    counts. Deleted (tombstoned) messages are ignored.
    """
    try:
        docs = await messages_collection().find({"chat_id": chat_id}).to_list(length=None)
        unread = 0
        for doc in docs:
            if str(doc.get("sender_id")) == str(viewer_id):
                continue  # My own messages are never unread for me.
            for message in _flatten_doc(doc):
                if message.get("deleted"):
                    continue
                created = message.get("created_at") or ""
                if not last_read_iso or created > last_read_iso:
                    unread += 1
        return unread
    except Exception:
        # Used when listing chats; log but don't crash the whole listing.
        logger.exception("count_unread failed for chat_id=%s.", chat_id)
        return 0


async def get_last_message(chat_id: str) -> dict | None:
    """Return the most recent message across all of a chat's user/day docs."""
    try:
        docs = await messages_collection().find({"chat_id": chat_id}).to_list(length=None)
        latest = None
        for doc in docs:
            for message in _flatten_doc(doc):
                if latest is None or (message["created_at"] or "") > (latest["created_at"] or ""):
                    latest = message
        return latest
    except Exception:
        # Used when listing chats; log but don't crash the whole listing.
        logger.exception("get_last_message failed for chat_id=%s.", chat_id)
        return None


def _parse_message_id(message_id: str) -> tuple[ObjectId, int] | None:
    """Split a flat message id ("<doc_id>:<order>") back into its parts."""
    try:
        doc_id_str, order_str = message_id.split(":", 1)
        return ObjectId(doc_id_str), int(order_str)
    except (ValueError, InvalidId, AttributeError):
        return None


async def edit_message(*, message_id: str, sender_id: str, content: str) -> dict | None:
    """Edit the text of a message the given user owns; returns the updated message."""
    try:
        col = messages_collection()
        parsed = _parse_message_id(message_id)
        if not parsed:
            logger.warning("edit_message: malformed message_id=%r.", message_id)
            return None
        doc_id, order = parsed

        doc = await col.find_one({"_id": doc_id})
        if not doc or doc.get("sender_id") != sender_id:
            logger.warning(
                "edit_message refused: message %s missing or not owned by %s.",
                message_id,
                sender_id,
            )
            return None  # Missing, or not the author -> refuse the edit.

        message = (doc.get("messages") or {}).get(str(order))
        if message is None or message.get("msg_type", "text") != "text":
            return None  # Only text messages can be edited.
        if message.get("deleted"):
            return None  # A deleted message can't be edited.

        edited_at = now_utc()
        await col.update_one(
            {"_id": doc_id},
            {"$set": {f"messages.{order}.content": content,
                      f"messages.{order}.edited_at": edited_at}},
        )

        updated = {**message, "content": content, "edited_at": edited_at}
        return _flatten(doc_id, doc["chat_id"], doc["sender_id"], doc.get("sender_username"), order, updated)
    except Exception:
        logger.exception(
            "edit_message failed (message_id=%s, sender_id=%s).", message_id, sender_id
        )
        raise


async def delete_message(*, message_id: str, requester_id: str, chat_id: str) -> dict | None:
    """Soft-delete a message; returns the tombstoned message.

    The sender can delete any of their own messages. In addition, *any* member
    of the chat may delete an **image** message, so both the user who sent a
    photo and the person who received it can remove it. We keep the entry (so
    message order and any replies stay intact) but clear its content and mark
    it deleted. Membership is validated by the WebSocket handler before we get
    here, and we double-check the message belongs to that chat below.
    """
    try:
        col = messages_collection()
        parsed = _parse_message_id(message_id)
        if not parsed:
            logger.warning("delete_message: malformed message_id=%r.", message_id)
            return None
        doc_id, order = parsed

        doc = await col.find_one({"_id": doc_id})
        if not doc:
            logger.warning("delete_message refused: message %s not found.", message_id)
            return None
        # The message must belong to the chat the requester is acting in.
        if doc.get("chat_id") != chat_id:
            logger.warning(
                "delete_message refused: message %s does not belong to chat %s.",
                message_id,
                chat_id,
            )
            return None

        message = (doc.get("messages") or {}).get(str(order))
        if message is None:
            return None

        is_owner = doc.get("sender_id") == requester_id
        is_image = message.get("msg_type") == "image"
        # Non-owners may only delete image messages.
        if not is_owner and not is_image:
            logger.warning(
                "delete_message refused: %s is not the owner of non-image message %s.",
                requester_id,
                message_id,
            )
            return None

        await col.update_one(
            {"_id": doc_id},
            {"$set": {
                f"messages.{order}.deleted": True,
                f"messages.{order}.content": "",
                f"messages.{order}.file_url": None,
                f"messages.{order}.file_name": None,
            }},
        )

        updated = {**message, "deleted": True, "content": "", "file_url": None, "file_name": None}
        return _flatten(doc_id, doc["chat_id"], doc["sender_id"], doc.get("sender_username"), order, updated)
    except Exception:
        logger.exception(
            "delete_message failed (message_id=%s, requester_id=%s).",
            message_id,
            requester_id,
        )
        raise
