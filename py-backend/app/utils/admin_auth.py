"""
Shared-secret guard for the /api/admin/* routes.

Every admin request must send `X-Admin-Key: <ADMIN_API_KEY>`. If ADMIN_API_KEY
is not configured the admin API is disabled entirely (fail closed) rather than
left open, since these routes can retrain and hot-swap the live search model.
"""

from __future__ import annotations

import hmac
import os

from fastapi import Header, HTTPException


def require_admin_key(x_admin_key: str | None = Header(default=None)) -> None:
    expected = os.getenv("ADMIN_API_KEY", "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Admin API is disabled. Set ADMIN_API_KEY in py-backend/.env and restart the server.",
        )
    if not x_admin_key or not hmac.compare_digest(x_admin_key.strip(), expected):
        raise HTTPException(status_code=401, detail="Invalid or missing admin key.")
