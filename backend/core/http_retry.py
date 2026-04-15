"""Shared HTTP retry + rate-limit awareness for Polymarket REST surfaces.

Policy:
- GET / idempotent operations: 5x exponential backoff with jitter on 5xx, 429,
  and httpx.RequestError (network).
- State-changing operations (POST/DELETE on CLOB trading) MUST NOT be auto-retried;
  use `classify_error` at the caller to decide.
- Rate-limit headers (`X-RateLimit-Remaining`, `X-RateLimit-Reset`, `Retry-After`)
  are honored when present; absent headers are a no-op (Polymarket does not
  currently emit them on 200 responses).
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any

import httpx
from loguru import logger


class TransientError(Exception):
    """Retryable failure -- 5xx, 429, or network error."""


class PermanentError(Exception):
    """Non-retryable failure -- 4xx (except 429) or caller-fixable problem."""


@dataclass(frozen=True)
class RetryConfig:
    max_retries: int = 5
    base_delay: float = 1.0
    max_delay: float = 16.0
    max_rate_limit_sleep: float = 30.0
    jitter: float = 0.2  # +/-20%


DEFAULT_CONFIG = RetryConfig()


def classify_error(e: Exception) -> type[Exception]:
    """Return TransientError or PermanentError for a given httpx exception."""
    if isinstance(e, httpx.HTTPStatusError):
        status = e.response.status_code
        if status == 429 or 500 <= status < 600:
            return TransientError
        return PermanentError
    if isinstance(e, httpx.RequestError):
        return TransientError
    return PermanentError


def _backoff_delay(attempt: int, cfg: RetryConfig) -> float:
    """Exponential backoff with +/-jitter."""
    delay = min(cfg.base_delay * (2**attempt), cfg.max_delay)
    spread = delay * cfg.jitter
    return max(0.0, delay + random.uniform(-spread, spread))


def _rate_limit_sleep_seconds(headers: httpx.Headers, cfg: RetryConfig) -> float:
    """If X-RateLimit-Remaining is low, return seconds to sleep until Reset.
    Returns 0 when headers are absent or remaining is comfortable."""
    remaining = headers.get("x-ratelimit-remaining")
    reset = headers.get("x-ratelimit-reset")
    if remaining is None or reset is None:
        return 0.0
    try:
        if int(remaining) >= 10:
            return 0.0
        reset_epoch = float(reset)
        sleep = max(0.0, reset_epoch - time.time())
        return min(sleep, cfg.max_rate_limit_sleep)
    except (ValueError, TypeError):
        return 0.0


def _retry_after_seconds(headers: httpx.Headers, cfg: RetryConfig) -> float | None:
    """Return Retry-After in seconds (capped), or None if absent/unparseable."""
    ra = headers.get("retry-after")
    if ra is None:
        return None
    try:
        return min(max(0.0, float(ra)), cfg.max_rate_limit_sleep)
    except (ValueError, TypeError):
        return None


async def fetch_json_with_retry(
    client: httpx.AsyncClient,
    endpoint: str,
    params: Any = None,
    cfg: RetryConfig | None = None,
) -> Any:
    """GET `endpoint` with retry on transient failures and rate-limit awareness.

    On success: returns parsed JSON.
    On permanent failure: raises PermanentError.
    On exhausted retries: raises TransientError.
    """
    cfg = cfg or DEFAULT_CONFIG
    last_exc: Exception | None = None

    for attempt in range(cfg.max_retries):
        try:
            resp = await client.get(endpoint, params=params)
            # Pre-emptive rate-limit backoff (no-op when headers absent)
            rl_sleep = _rate_limit_sleep_seconds(resp.headers, cfg)
            if rl_sleep > 0:
                logger.debug(
                    f"Rate limit low on {endpoint}; pre-emptive sleep {rl_sleep:.1f}s"
                )
                await asyncio.sleep(rl_sleep)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            last_exc = e
            if classify_error(e) is PermanentError:
                raise PermanentError(
                    f"{endpoint}: HTTP {e.response.status_code}: {e}"
                ) from e
            # Transient: use Retry-After if present, else exponential backoff
            ra = _retry_after_seconds(e.response.headers, cfg)
            delay = ra if ra is not None else _backoff_delay(attempt, cfg)
            if attempt == cfg.max_retries - 1:
                break
            logger.warning(
                f"Retry {attempt + 1}/{cfg.max_retries} {endpoint} "
                f"(HTTP {e.response.status_code}, sleeping {delay:.1f}s)"
            )
            await asyncio.sleep(delay)
        except httpx.RequestError as e:
            last_exc = e
            delay = _backoff_delay(attempt, cfg)
            if attempt == cfg.max_retries - 1:
                break
            logger.warning(
                f"Retry {attempt + 1}/{cfg.max_retries} {endpoint} "
                f"(network error: {e}, sleeping {delay:.1f}s)"
            )
            await asyncio.sleep(delay)

    raise TransientError(
        f"{endpoint}: exhausted {cfg.max_retries} retries: {last_exc}"
    ) from last_exc
