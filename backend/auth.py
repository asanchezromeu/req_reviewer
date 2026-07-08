"""Static API-key auth for /api/v1 routes.

Minimal by design (SPEC.md: "Auth from day one, minimal"): a comma-separated
API_KEYS env var checked against an Authorization: Bearer header. If API_KEYS
is unset, auth is disabled with a startup warning — mirrors the existing
Mongo-or-memory graceful-degradation pattern rather than hard-failing.
"""

import logging
import os

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger("reqiq")

_bearer_scheme = HTTPBearer(auto_error=False)


def _configured_keys() -> set:
    raw = os.environ.get("API_KEYS", "")
    return {key.strip() for key in raw.split(",") if key.strip()}


_WARNED = False


async def require_api_key(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> None:
    global _WARNED
    keys = _configured_keys()
    if not keys:
        if not _WARNED:
            logger.warning("API_KEYS is not configured; /api/v1 routes are unauthenticated.")
            _WARNED = True
        return

    if credentials is None or credentials.credentials not in keys:
        raise HTTPException(status_code=401, detail="Missing or invalid API key")
