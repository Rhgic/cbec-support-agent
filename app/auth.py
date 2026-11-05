"""Shared bearer authentication for operator APIs."""

import secrets

from fastapi import Header, HTTPException

from app.config import get_settings


def require_token(authorization: str | None = Header(default=None)) -> str:
    token = get_settings().demo_token
    expected = f"Bearer {token}"
    if not authorization or not secrets.compare_digest(authorization.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="无效或缺失 token")
    return token
