"""Authenticated operator-only Amazon diagnostics and order lookup."""

from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_token
from app.services.amazon import AmazonError, get_amazon_client

router = APIRouter(prefix="/integrations/amazon", tags=["amazon"], dependencies=[Depends(require_token)])


@router.get("/status")
def amazon_status():
    return get_amazon_client().readiness()


def _call(fn, order_id):
    try:
        return fn(order_id)
    except AmazonError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None


@router.get("/orders/{order_id}")
def amazon_order(order_id: str):
    return _call(get_amazon_client().order_summary, order_id)


@router.get("/orders/{order_id}/message-actions")
def amazon_message_actions(order_id: str):
    return _call(get_amazon_client().message_actions, order_id)
