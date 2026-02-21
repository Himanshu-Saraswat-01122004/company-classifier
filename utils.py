"""
utils.py - Shared helper utilities.
"""

import asyncio
import collections
import json
import logging
import re
import time
from typing import Any

logger = logging.getLogger(__name__)


# ── JSON parsing ──────────────────────────────────────────────────────────────

def safe_parse_json(raw: str) -> dict[str, Any] | None:
    """
    Safely extract and parse JSON from an LLM response string.

    Handles cases where the model wraps JSON in markdown code-fences.

    Args:
        raw: Raw string output from the LLM.

    Returns:
        Parsed dictionary, or None if parsing fails.
    """
    if not raw:
        return None

    # Strip markdown code fences if present
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip()
    cleaned = cleaned.rstrip("`").strip()

    # Try direct parse first
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Try to extract first {...} block
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            logger.warning("JSON extraction fallback also failed.")

    logger.error("Could not parse JSON from response: %s", raw[:200])
    return None


# ── Field validation / normalisation ─────────────────────────────────────────

VALID_DOMAINS = {"ECE", "CSE", "BOTH", "NEITHER"}
VALID_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}
VALID_YES_NO = {"YES", "NO", "UNKNOWN"}
VALID_HW_SW = {"Hardware", "Software", "Both", "Neither"}


def normalise_field(value: Any, valid_set: set[str], default: str = "UNKNOWN") -> str:
    """
    Normalise a string field against an allowed set.

    Args:
        value:     Raw value from the parsed JSON.
        valid_set: Set of accepted strings (case-insensitive match).
        default:   Fallback if no match found.

    Returns:
        Matched canonical string from valid_set, or default.
    """
    if not value:
        return default
    val_str = str(value).strip()
    for candidate in valid_set:
        if candidate.lower() == val_str.lower():
            return candidate
    logger.debug("Field value %r not in %s; defaulting to %r", val_str, valid_set, default)
    return default


def build_fallback_result(company_name: str, reason: str = "Classification failed") -> dict[str, Any]:
    """
    Return a safe default result dict when classification cannot be completed.

    Args:
        company_name: Name of the company.
        reason:       Human-readable error description.

    Returns:
        Dictionary with all required classification fields set to safe defaults.
    """
    return {
        "domain": "NEITHER",
        "confidence": "LOW",
        "primary_domain_area": "UNKNOWN",
        "hardware_or_software": "Neither",
        "hiring_possible": "UNKNOWN",
        "fresher_friendly": "UNKNOWN",
        "likely_roles": "UNKNOWN",
        "reason": reason,
    }


# ── API Key Pool ──────────────────────────────────────────────────────────────

class ApiKeyPool:
    """
    Round-robin pool of Gemini API keys with per-key 429 backoff tracking.

    When a key gets a 429, it is put into a cooldown for ``retry_after``
    seconds and the pool automatically switches to the next available key.
    If ALL keys are in cooldown, ``acquire_key()`` sleeps until the earliest
    one becomes available again.

    Args:
        keys: List of API key strings (at least one required).

    Usage::

        pool = ApiKeyPool(["key1", "key2", "key3"])
        key = await pool.acquire_key()
        # ... make API call with key ...
        # on 429:
        pool.mark_exhausted(key, retry_after=55.0)
    """

    def __init__(self, keys: list[str]) -> None:
        if not keys:
            raise ValueError("ApiKeyPool requires at least one API key.")
        self._keys: list[str] = list(keys)
        # key → monotonic time when it becomes available again (0 = immediately)
        self._available_at: dict[str, float] = {k: 0.0 for k in keys}
        self._next_index: int = 0          # round-robin cursor
        self._lock = asyncio.Lock()
        logger.info("ApiKeyPool initialised with %d key(s) (round-robin).", len(keys))

    async def acquire_key(self) -> str:
        """
        Return the next available API key using round-robin.

        Cycles through keys in order (key1→key2→key3→key1…).  If the next
        key in the rotation is currently rate-limited, it skips to the next
        one.  If ALL keys are in cooldown, sleeps until the earliest one
        recovers.

        Returns:
            An API key string that is not currently rate-limited.
        """
        async with self._lock:
            n = len(self._keys)
            while True:
                now = time.monotonic()
                # Try each key starting at the round-robin cursor
                for offset in range(n):
                    idx = (self._next_index + offset) % n
                    key = self._keys[idx]
                    if now >= self._available_at[key]:
                        # Advance cursor for the next call
                        self._next_index = (idx + 1) % n
                        return key

                # All keys in cooldown — sleep until the soonest recovers
                next_free = min(self._available_at.values())
                wait = next_free - now + 0.1
                logger.warning(
                    "All %d key(s) rate-limited. Sleeping %.1fs…",
                    n, wait,
                )
                self._lock.release()
                try:
                    await asyncio.sleep(wait)
                finally:
                    await self._lock.acquire()

    def mark_exhausted(self, key: str, retry_after: float = 65.0) -> None:
        """
        Mark a key as rate-limited for ``retry_after`` seconds.

        Args:
            key:         The API key that received a 429.
            retry_after: Seconds to wait before using this key again.
        """
        self._available_at[key] = time.monotonic() + retry_after
        # Rotate key to end so other keys are tried first
        if key in self._keys:
            self._keys.remove(key)
            self._keys.append(key)
        active = sum(
            1 for k, t in self._available_at.items() if time.monotonic() >= t
        )
        logger.warning(
            "Key …%s rate-limited for %.0fs. Active keys: %d/%d.",
            key[-6:], retry_after, active, len(self._keys),
        )

    def has_available_key(self) -> bool:
        """Return True if at least one key is not in cooldown right now."""
        now = time.monotonic()
        return any(now >= t for t in self._available_at.values())


# ── Rate limiter ─────────────────────────────────────────────────────────────

class RateLimiter:
    """
    Async token-bucket rate limiter with even spacing.

    Instead of allowing a full burst at the start of each window, it enforces
    a minimum interval between consecutive requests (``60 / max_calls`` seconds).
    This prevents the initial stampede that causes 429 errors.

    All coroutines share a single asyncio.Lock, so only one request is admitted
    at a time; excess callers sleep and retry automatically.

    Args:
        max_calls:       Maximum requests per 60-second window (default 14).
        period_seconds:  Window size (default 60.0 s).
    """

    def __init__(self, max_calls: int = 14, period_seconds: float = 60.0) -> None:
        self._max_calls = max_calls
        self._period = period_seconds
        # Minimum gap between any two consecutive requests
        self._min_interval: float = period_seconds / max_calls
        self._last_sent: float = 0.0          # monotonic time of last granted slot
        self._timestamps: collections.deque[float] = collections.deque()
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> "RateLimiter":
        await self.acquire()
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass

    async def acquire(self) -> None:
        """
        Block until a rate-limited slot is available.

        Enforces two constraints simultaneously:
        1. Sliding window: no more than ``max_calls`` in any 60-second window.
        2. Minimum interval: at least ``60/max_calls`` seconds between requests.

        This combination prevents both sustained over-limit AND burst spikes.
        """
        async with self._lock:
            while True:
                now = time.monotonic()

                # Evict expired timestamps from the sliding window
                while self._timestamps and now - self._timestamps[0] >= self._period:
                    self._timestamps.popleft()

                window_ok = len(self._timestamps) < self._max_calls
                time_since_last = now - self._last_sent
                spacing_ok = time_since_last >= self._min_interval

                if window_ok and spacing_ok:
                    self._timestamps.append(now)
                    self._last_sent = now
                    return  # slot granted

                # Calculate how long to sleep
                waits: list[float] = []
                if not spacing_ok:
                    waits.append(self._min_interval - time_since_last)
                if not window_ok:
                    waits.append(self._period - (now - self._timestamps[0]) + 0.05)
                wait_for = max(waits)

                logger.debug(
                    "Rate limiter: window=%d/%d spacing_ok=%s → sleeping %.2fs",
                    len(self._timestamps), self._max_calls, spacing_ok, wait_for,
                )
                self._lock.release()
                try:
                    await asyncio.sleep(wait_for)
                finally:
                    await self._lock.acquire()

    def update_after_429(self, retry_after: float = 10.0) -> None:
        """
        Called when a 429 is received despite the limiter.

        Pushes ``_last_sent`` forward so the limiter backs off immediately.

        Args:
            retry_after: Seconds to pause (from Retry-After header, default 10).
        """
        self._last_sent = time.monotonic() + retry_after
        logger.warning(
            "429 back-off applied: pausing new requests for %.1fs.", retry_after
        )

    @property
    def max_calls(self) -> int:
        """Configured maximum calls per window."""
        return self._max_calls

    @property
    def period_seconds(self) -> float:
        """Configured window size in seconds."""
        return self._period


# ── Timing utilities ──────────────────────────────────────────────────────────

class Timer:
    """Simple wall-clock timer for measuring execution time."""

    def __init__(self) -> None:
        self._start: float = 0.0
        self._end: float = 0.0

    def __enter__(self) -> "Timer":
        self._start = time.perf_counter()
        return self

    def __exit__(self, *_: Any) -> None:
        self._end = time.perf_counter()

    @property
    def elapsed_seconds(self) -> float:
        """Elapsed time in seconds."""
        return self._end - self._start

    @property
    def elapsed_str(self) -> str:
        """Human-readable elapsed time."""
        secs = self.elapsed_seconds
        mins, secs = divmod(int(secs), 60)
        return f"{mins}m {secs}s" if mins else f"{secs}s"


# ── Logging setup ─────────────────────────────────────────────────────────────

def setup_logging(level: str = "INFO") -> None:
    """
    Configure root logger with a consistent format.

    Args:
        level: Logging level string (e.g. 'DEBUG', 'INFO', 'WARNING').
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Silence noisy third-party loggers
    for lib in ("httpx", "httpcore", "aiohttp", "asyncio"):
        logging.getLogger(lib).setLevel(logging.WARNING)
