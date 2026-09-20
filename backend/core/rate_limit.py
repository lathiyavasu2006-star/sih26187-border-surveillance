"""Rate limiting.

* Per-route limits (login, refresh, profile, password change) use slowapi's `@limiter.limit` decorator.
* The global per-client limit (RATE_LIMIT_PER_MINUTE) is enforced by `RateLimitMiddleware`, a pure ASGI
  middleware. It replaces slowapi's SlowAPIASGIMiddleware, which had two defects in this stack:
    1. it re-sent `http.response.start` before every body chunk, so uvicorn raised
       "Expected ASGI message 'http.response.body'" on streamed responses;
    2. it looked handlers up only in `app.routes`; FastAPI 0.141 nests included routers, so every router
       endpoint was treated as exempt and the global limit silently applied to `/` and `/ping` only.
"""
import time
from typing import Dict

from limits import RateLimitItem, parse
from limits.strategies import MovingWindowRateLimiter
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from backend.core.api_config import api_settings

GLOBAL_NAMESPACE = "global-per-client"

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[api_settings.default_rate_limit],
    headers_enabled=False,
    storage_uri="memory://",
)


class RateLimitMiddleware:
    """Moving-window limit of RATE_LIMIT_PER_MINUTE requests per client across every HTTP endpoint.

    Shares slowapi's storage, so `limiter.reset()` and `limiter.enabled` control both mechanisms.
    CORS preflight (OPTIONS) is not counted: browsers send it automatically before real requests."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._items: Dict[int, RateLimitItem] = {}

    def _item(self) -> RateLimitItem:
        per_minute = int(api_settings.RATE_LIMIT_PER_MINUTE)
        item = self._items.get(per_minute)
        if item is None:
            item = parse(f"{per_minute}/minute")
            self._items[per_minute] = item
        return item

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") == "OPTIONS" or not limiter.enabled:
            await self.app(scope, receive, send)
            return

        from backend.core.dependencies import get_client_ip  # honours TRUSTED_PROXIES

        key = get_client_ip(Request(scope))
        item = self._item()
        strategy = MovingWindowRateLimiter(limiter._storage)
        if strategy.hit(item, GLOBAL_NAMESPACE, key):
            await self.app(scope, receive, send)
            return

        reset_at, _ = strategy.get_window_stats(item, GLOBAL_NAMESPACE, key)
        retry_after = max(1, int(round(reset_at - time.time())))
        response = JSONResponse(
            {"detail": f"Rate limit exceeded: {item}"},
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )
        await response(scope, receive, send)
