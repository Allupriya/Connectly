"""Web Push notifications via VAPID.

Stores each browser's push subscription in MongoDB and fans out notifications
to a user's devices. Sending uses ``pywebpush`` (which is synchronous, so we run
it in a worker thread to avoid blocking the event loop). Stale subscriptions
(HTTP 404/410 from the push service) are pruned automatically.

If VAPID keys aren't configured (see settings), all functions become no-ops so
the rest of the app keeps working without push.
"""

import asyncio
import json

from pywebpush import WebPushException, webpush

from ..config import get_logger, settings
from ..db import push_subscriptions_collection
from ..models import now_utc

logger = get_logger(__name__)


async def save_subscription(user_id: str, subscription: dict) -> None:
    """Upsert a browser push subscription for a user (keyed by its endpoint)."""
    endpoint = (subscription or {}).get("endpoint")
    if not endpoint:
        logger.warning("save_subscription: missing endpoint (user %s).", user_id)
        return
    try:
        await push_subscriptions_collection().update_one(
            {"endpoint": endpoint},
            {"$set": {
                "endpoint": endpoint,
                "user_id": user_id,
                "subscription": subscription,
                "updated_at": now_utc(),
            }},
            upsert=True,
        )
        logger.info("Saved push subscription for user %s.", user_id)
    except Exception:
        logger.exception("save_subscription failed for user %s.", user_id)


async def remove_subscription(endpoint: str) -> None:
    """Delete a subscription (e.g. after the client unsubscribes or it goes stale)."""
    if not endpoint:
        return
    try:
        await push_subscriptions_collection().delete_one({"endpoint": endpoint})
        logger.info("Removed push subscription %s.", endpoint[:40])
    except Exception:
        logger.exception("remove_subscription failed.")


def _send_one(subscription: dict, data: str) -> None:
    """Blocking send of a single web push (runs in a worker thread)."""
    webpush(
        subscription_info=subscription,
        data=data,
        vapid_private_key=settings.VAPID_PRIVATE_KEY,
        vapid_claims={"sub": settings.VAPID_SUBJECT},
    )


async def send_to_users(user_ids, payload: dict) -> None:
    """Send a push notification to every stored device of the given users."""
    if not settings.push_enabled:
        return  # Push disabled (no VAPID keys) -> silently skip.

    user_ids = [str(u) for u in set(user_ids)]
    if not user_ids:
        return

    try:
        docs = await push_subscriptions_collection().find(
            {"user_id": {"$in": user_ids}}
        ).to_list(length=None)
    except Exception:
        logger.exception("send_to_users: failed to load subscriptions.")
        return

    data = json.dumps(payload)
    for doc in docs:
        subscription = doc.get("subscription")
        endpoint = doc.get("endpoint")
        if not subscription:
            continue
        try:
            await asyncio.to_thread(_send_one, subscription, data)
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None)
            if status in (404, 410):
                # Subscription is gone; drop it so we stop trying.
                logger.info("Pruning stale push subscription (%s).", status)
                await remove_subscription(endpoint)
            else:
                logger.warning("Web push failed (%s): %s", status, exc)
        except Exception:
            logger.exception("Unexpected error sending web push.")
