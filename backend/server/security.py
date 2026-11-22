"""Browser-origin protection for the local API.

The API can move money (buy-pair, sell, merge, exit) and has no login. Two
layers keep other websites open in the same browser from using it:

1. CORS only allows the dashboard's own origins, so foreign pages can't read
   responses.
2. CORS alone doesn't stop a cross-site "simple" POST from *executing*
   (e.g. a form post to /positions/{id}/exit, which has no body). So any
   state-changing request that carries a foreign `Origin` header is refused
   with 403. Browsers always send `Origin` on cross-origin POSTs; local
   scripts and agent skills (curl, httpx) send none and still work.

The Next.js `/api` proxy forwards the browser's `Origin`, so dashboard
requests arrive with the allowed origin.
"""

import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

DEFAULT_ALLOWED_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def parse_allowed_origins(raw: str | None) -> list[str]:
    """Comma-separated origins from ALLOWED_ORIGINS (or the defaults)."""
    value = raw if raw else DEFAULT_ALLOWED_ORIGINS
    return [o.strip().rstrip("/") for o in value.split(",") if o.strip()]


def is_request_allowed(method: str, origin: str | None, allowed: list[str]) -> bool:
    """Refuse state-changing requests from foreign browser origins."""
    if method.upper() in SAFE_METHODS or origin is None:
        return True
    return origin.rstrip("/") in allowed


def add_origin_protection(app: FastAPI, allowed: list[str] | None = None) -> None:
    """Install the CORS allow-list and the foreign-origin write guard."""
    origins = (
        allowed
        if allowed is not None
        else parse_allowed_origins(os.getenv("ALLOWED_ORIGINS"))
    )

    @app.middleware("http")
    async def reject_foreign_origin(request: Request, call_next):
        if not is_request_allowed(
            request.method, request.headers.get("origin"), origins
        ):
            return JSONResponse(
                status_code=403,
                content={"detail": "Origin not allowed"},
            )
        return await call_next(request)

    # Added last so it wraps the guard: preflights are answered here.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
