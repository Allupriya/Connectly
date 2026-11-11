"""Web Push endpoints: hand out the VAPID public key and store subscriptions."""

from fastapi import APIRouter, Body, Depends, HTTPException

from ..config import get_logger, settings
from ..services import push
from .users import get_current_user

logger = get_logger(__name__)

router = APIRouter(prefix="/api/push", tags=["Notifications"])


@router.get("/public-key", summary="Get the VAPID public key for push subscriptions")
async def public_key():
    # Safe to expose: the public key is meant to be shared with browsers.
    return {"public_key": settings.VAPID_PUBLIC_KEY, "enabled": settings.push_enabled}


@router.post("/subscribe", summary="Register this browser for push notifications")
async def subscribe(
    subscription: dict = Body(...), current=Depends(get_current_user)
):
    try:
        if not settings.push_enabled:
            raise HTTPException(status_code=503, detail="Push notifications are disabled")
        if not subscription.get("endpoint"):
            raise HTTPException(status_code=400, detail="Invalid subscription")
        await push.save_subscription(str(current["_id"]), subscription)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception:
        logger.exception("push subscribe failed for user %s.", current.get("_id"))
        raise HTTPException(status_code=500, detail="Failed to save subscription")


@router.post("/unsubscribe", summary="Remove this browser's push subscription")
async def unsubscribe(
    subscription: dict = Body(...), current=Depends(get_current_user)
):
    try:
        await push.remove_subscription(subscription.get("endpoint", ""))
        return {"ok": True}
    except HTTPException:
        raise
    except Exception:
        logger.exception("push unsubscribe failed for user %s.", current.get("_id"))
        raise HTTPException(status_code=500, detail="Failed to remove subscription")
