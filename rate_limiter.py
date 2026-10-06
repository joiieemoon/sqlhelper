"""
rate_limiter.py
---------------
Device-ID based rate limiting backed by SQLite.
- 5 queries per 24-hour rolling window per device
- Identifier: X-Device-ID header (Chrome extension UUID, per installation)
  Falls back to client IP if header is missing
- Thread-safe via a module-level lock
- WAL mode SQLite for safe concurrent access
- Rolling 24hr window from first query, not midnight
"""

import sqlite3
import threading
import time
import os

# ── Config ────────────────────────────────────────────────────────────

# Strictly 5 optimizations per device per rolling 24h window. Overridable only
# for local testing via the RATE_LIMIT_MAX env var; the default is the product
# limit and is what every response reports back to the client.
RATE_LIMIT_MAX        = int(os.getenv("RATE_LIMIT_MAX", "5"))
RATE_LIMIT_WINDOW_SEC = 24 * 60 * 60      # 24 hours in seconds
DB_PATH               = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "rate_limit.db"
)

# ── Internal state ────────────────────────────────────────────────────

_lock = threading.Lock()


# ── DB bootstrap ──────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _bootstrap():
    """Create table on first run. Idempotent."""
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rate_limits (
                identifier   TEXT    PRIMARY KEY,
                count        INTEGER NOT NULL DEFAULT 0,
                window_start REAL    NOT NULL
            )
        """)
        conn.commit()


_bootstrap()


# ── Public API ────────────────────────────────────────────────────────

def _window_state(identifier: str, now: float):
    """
    Read the caller's current window without mutating it.

    Returns (count, window_start, window_age). count is the number of queries
    already consumed inside the active window.
    """
    row = _get_conn().execute(
        "SELECT count, window_start FROM rate_limits WHERE identifier = ?",
        (identifier,),
    ).fetchone()
    if row is None:
        return 0, None, None
    count, window_start = row
    return count, window_start, now - window_start


def _limit_payload(used: int, resets_in: float) -> dict:
    """Uniform counter block attached to every rate-limit-aware response."""
    remaining = max(0, RATE_LIMIT_MAX - used)
    return {
        "limit":            RATE_LIMIT_MAX,
        "queries_used":     used,
        "queries_remaining": remaining,
        "resets_in":        int(resets_in) if resets_in is not None else None,
    }


def get_usage(identifier: str) -> dict:
    """
    Report current usage WITHOUT consuming a query.

    Used by the "Test Connection" button and the UI's counter display so a
    credential check never burns one of the user's 5 daily optimizations.
    """
    now = time.time()
    with _lock:
        count, window_start, window_age = _window_state(identifier, now)

    if count == 0 or window_age is None:
        # No active window yet — nothing consumed.
        return dict(_limit_payload(0, RATE_LIMIT_WINDOW_SEC), allowed=True)
    if window_age >= RATE_LIMIT_WINDOW_SEC:
        # Window already expired; next real query starts a fresh one.
        return dict(_limit_payload(0, RATE_LIMIT_WINDOW_SEC), allowed=True)

    resets_in = RATE_LIMIT_WINDOW_SEC - window_age
    payload = _limit_payload(count, resets_in)
    payload["allowed"] = count < RATE_LIMIT_MAX
    if not payload["allowed"]:
        hours = int(resets_in // 3600)
        minutes = int((resets_in % 3600) // 60)
        time_str = f"{hours}h {minutes}m" if hours else f"{minutes}m"
        payload["error"] = (
            f"Daily limit of {RATE_LIMIT_MAX} queries reached. Resets in {time_str}."
        )
    return payload


def consume(identifier: str) -> dict:
    """
    Decrement the caller by exactly one query, and return the new counters.

    Called AFTER the LLM stage actually ran, so a request that is rejected
    earlier (bad SQL, missing query, server busy) never costs the user one of
    their daily optimizations. Callers must gate entry with get_usage() to
    fail fast without spending.

    Returns the same shape as check_and_increment(). `allowed` is False only if
    the window was already exhausted by a concurrent request.
    """
    now = time.time()

    with _lock:
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT count, window_start FROM rate_limits WHERE identifier = ?",
                (identifier,),
            ).fetchone()

            if row is None or (now - row[1]) >= RATE_LIMIT_WINDOW_SEC:
                # First query, or the previous window expired.
                conn.execute(
                    "INSERT INTO rate_limits (identifier, count, window_start) VALUES (?, 1, ?) "
                    "ON CONFLICT(identifier) DO UPDATE SET count = 1, window_start = excluded.window_start",
                    (identifier, now),
                )
                conn.commit()
                return dict(_limit_payload(1, RATE_LIMIT_WINDOW_SEC), allowed=True)

            count, window_start = row
            resets_in = RATE_LIMIT_WINDOW_SEC - (now - window_start)

            if count >= RATE_LIMIT_MAX:
                # Lost a race with a concurrent request that took the last slot.
                hours = int(resets_in // 3600)
                minutes = int((resets_in % 3600) // 60)
                time_str = f"{hours}h {minutes}m" if hours else f"{minutes}m"
                return dict(
                    _limit_payload(RATE_LIMIT_MAX, resets_in),
                    allowed=False,
                    error=(
                        f"Daily limit of {RATE_LIMIT_MAX} queries reached. "
                        f"Resets in {time_str}."
                    ),
                )

            new_count = count + 1
            conn.execute(
                "UPDATE rate_limits SET count = ? WHERE identifier = ?",
                (new_count, identifier),
            )
            conn.commit()
            return dict(_limit_payload(new_count, resets_in), allowed=True)


def check_and_increment(identifier: str) -> dict:
    """
    Check and increment query count for this identifier.

    Allowed response:
        {
            "allowed":           True,
            "limit":             RATE_LIMIT_MAX,
            "queries_used":      N,
            "queries_remaining": RATE_LIMIT_MAX - N,
            "resets_in":         <seconds until window expires>,
        }

    Blocked response:
        {
            "allowed":    False,
            "error":      "<human-readable message with time remaining>",
            "limit":             RATE_LIMIT_MAX,
            "queries_used":      RATE_LIMIT_MAX,
            "queries_remaining": 0,
            "resets_in":  <seconds until window expires (int)>,
        }
    """
    now = time.time()

    with _lock:
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT count, window_start FROM rate_limits WHERE identifier = ?",
                (identifier,)
            ).fetchone()

            if row is None:
                # First ever request from this device
                conn.execute(
                    "INSERT INTO rate_limits (identifier, count, window_start) VALUES (?, 1, ?)",
                    (identifier, now)
                )
                conn.commit()
                return dict(
                    _limit_payload(1, RATE_LIMIT_WINDOW_SEC),
                    allowed=True,
                )

            count, window_start = row
            window_age = now - window_start

            if window_age >= RATE_LIMIT_WINDOW_SEC:
                # Window expired — reset and allow
                conn.execute(
                    "UPDATE rate_limits SET count = 1, window_start = ? WHERE identifier = ?",
                    (now, identifier)
                )
                conn.commit()
                return dict(
                    _limit_payload(1, RATE_LIMIT_WINDOW_SEC),
                    allowed=True,
                )

            if count >= RATE_LIMIT_MAX:
                # Still within window and limit hit — block
                resets_in = RATE_LIMIT_WINDOW_SEC - window_age
                hours     = int(resets_in // 3600)
                minutes   = int((resets_in % 3600) // 60)
                time_str  = f"{hours}h {minutes}m" if hours else f"{minutes}m"
                return dict(
                    _limit_payload(RATE_LIMIT_MAX, resets_in),
                    allowed=False,
                    error=(
                        f"Daily limit of {RATE_LIMIT_MAX} queries reached. "
                        f"Resets in {time_str}."
                    ),
                )

            # Within window and under limit — increment and allow
            new_count = count + 1
            conn.execute(
                "UPDATE rate_limits SET count = ? WHERE identifier = ?",
                (new_count, identifier)
            )
            conn.commit()
            return dict(
                _limit_payload(new_count, RATE_LIMIT_WINDOW_SEC - window_age),
                allowed=True,
            )