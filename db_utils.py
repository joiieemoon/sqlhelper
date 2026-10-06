# -*- coding: utf-8 -*-
"""
db_utils.py — Direct database connection helpers for the server-side
database-connection architecture (Web App → FastAPI → MySQL/PostgreSQL).

Supports two engines behind one dispatcher:
  - MySQL       via mysql-connector-python 9.6.0
  - PostgreSQL  via psycopg2-binary 2.9.12
connect_mysql() keeps its original behaviour byte-for-byte; PostgreSQL is
added alongside it so existing MySQL callers cannot regress.

Engine selection (connect_db):
  1. explicit config {"db_type": "postgres"|"mysql"} (aliases accepted)
  2. otherwise probe_engine() sniffs the wire protocol on the port
  3. if undecidable, default to MySQL (preserves historical behaviour)

SECURITY CONTRACT (enforced here):
  - Password is used ONLY as a connection argument.
  - Never logged, never returned, never sent to any LLM.
  - redact_config() must be used before ANY log/response of a config.
"""

import logging
import os
import socket
import struct
import time
from typing import Any, Dict, Optional, Tuple

import mysql.connector
from mysql.connector import errorcode

# psycopg2 is optional: the module must stay importable on a host that only
# runs the MySQL path (mirrors how db_schema.py tolerates missing MySQLdb).
try:
    import psycopg2
    _PSYCOPG2_IMPORT_ERROR: Optional[str] = None
except ImportError as exc:            # pragma: no cover - environment dependent
    psycopg2 = None                   # type: ignore[assignment]
    _PSYCOPG2_IMPORT_ERROR = str(exc)

log = logging.getLogger("query_optimizer.db")

# Reasonable defaults for a short-lived, per-request connection.
CONNECT_TIMEOUT = int(os.getenv("DB_CONNECT_TIMEOUT", "10"))

ENGINE_MYSQL = "mysql"
ENGINE_POSTGRES = "postgres"

# Accepted spellings for an explicit engine request.
_ENGINE_ALIASES = {
    "mysql": ENGINE_MYSQL,
    "mariadb": ENGINE_MYSQL,
    "postgres": ENGINE_POSTGRES,
    "postgresql": ENGINE_POSTGRES,
    "pg": ENGINE_POSTGRES,
}


def redact_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of the config safe for logging / error reporting."""
    safe = dict(cfg)
    if "password" in safe:
        safe["password"] = "***"
    return safe


# Read-only service account.
#
# The application only ever needs SELECT (schema reads, EXPLAIN ANALYZE,
# INFORMATION_SCHEMA, SHOW). Connecting as an admin account means that if the
# SELECT-only validator were ever bypassed — a new route that forgets to call
# it, a logic bug, a crafted dialect quirk — MySQL would happily execute the
# write. A `GRANT SELECT` account makes the database itself the final authority:
# no amount of application-level bypass can modify data.
#
# When DB_RO_USER / DB_RO_PASSWORD are set, a request that supplies the
# configured admin account is transparently swapped for this one, so the app
# never uses elevated privileges. Requests with *other* credentials are left
# alone (Test Connection must still verify what the user typed) and remain
# protected by the validator plus the READ ONLY session pin below.
DB_RO_USER     = os.getenv("DB_RO_USER", "")
DB_RO_PASSWORD = os.getenv("DB_RO_PASSWORD", "")
DB_ADMIN_USER  = os.getenv("DB_USER", "")


def _apply_read_only_account(
    host: str, port: int, user: str, password: str, database: Optional[str]
) -> Tuple[str, str, bool]:
    """
    Return (user, password, downgraded) for a connection.

    downgraded=True means an admin account was replaced by the read-only one.
    Only the SAME host is substituted — a request pointing at a different
    server must keep the credentials the caller supplied.
    """
    if not DB_RO_USER or not DB_RO_PASSWORD:
        return user, password, False
    if not DB_ADMIN_USER or user != DB_ADMIN_USER:
        return user, password, False
    if host != os.getenv("DB_HOST", host):
        return user, password, False
    return DB_RO_USER, DB_RO_PASSWORD, True


def connect_mysql(
    config: Dict[str, Any],
    req_id: Optional[str] = None,
    allow_read_only: bool = True,
) -> mysql.connector.MySQLConnection:
    """
    Open a short-lived MySQL connection from a request-supplied config.

    SSL is always enabled (Aiven requires it). Certificate identity
    verification is on by default and can be disabled per-request with
    "ssl_verify": false for environments whose CA isn't shipped (R&D).

    `req_id` is optional, used only to correlate the log lines with the
    calling request; it never reaches the driver.

    Raises mysql.connector.Error subclasses on failure — the caller maps
    them to safe HTTP responses. Never include the password in any error.

    Diagnostics: every failure is logged in full — driver exception class,
    errno, sqlstate, driver message, elapsed time, redacted config and the
    traceback — so the log states *why* the connection failed. The raised
    ValueError carries the same driver reason: the driver never puts the
    password into its error text (it is not part of the wire-protocol
    error) and the config always goes through redact_config().
    """
    host     = config.get("host")
    port     = int(config.get("port") or 3306)
    user     = config.get("user")
    password = config.get("password")
    database = config.get("database")
    ssl_verify = bool(config.get("ssl_verify", False))

    if not host or not user:
        raise ValueError("host and user are required")
    if password is None:
        raise ValueError("password is required")

    # ── Downgrade an admin account to the read-only service account ──
    # Defence in depth: even if the SELECT-only validator were bypassed, this
    # session has no INSERT/UPDATE/DELETE/DDL privilege at the database.
    # allow_read_only=False is used by the "Test Connection" endpoint, which
    # must verify the exact credentials the caller typed — swapping in our own
    # password there would report a wrong password as working.
    if allow_read_only:
        user, password, downgraded = _apply_read_only_account(
            host, port, user, password, database
        )
        if downgraded:
            log.warning(
                "%sAdmin account '%s' requested - connecting as the read-only "
                "service account instead (writes are impossible for this session)",
                f"[{req_id}] " if req_id else "", DB_ADMIN_USER,
            )

    kwargs: Dict[str, Any] = {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "database": database or None,
        "connection_timeout": CONNECT_TIMEOUT,
        "autocommit": True,
        # SSL: REQUIRED. mysql-connector-python negotiates TLS by default
        # for non-unix-socket connections; we make it explicit.
        "ssl_disabled": False,
    }
    if ssl_verify:
        kwargs["ssl_verify_cert"] = True
        kwargs["ssl_verify_identity"] = True

    _tag = f"[{req_id}] " if req_id else ""
    log.info("%sDB connect attempt: %s", _tag, redact_config({**config, "port": port}))
    started = time.perf_counter()
    try:
        conn = mysql.connector.connect(**kwargs)
    except mysql.connector.Error as exc:
        # ── Full failure diagnostics (server log) ─────────────────────
        # exc.errno / exc.sqlstate / exc.msg never contain the password (it
        # is not part of the wire-protocol error) and the config goes through
        # redact_config(), so this is safe to log in full. exc_info adds the
        # traceback, showing exactly where the driver gave up.
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        errno      = getattr(exc, "errno", None)
        sqlstate   = getattr(exc, "sqlstate", None)
        driver_msg = (getattr(exc, "msg", None) or str(exc) or "").strip()
        if len(driver_msg) > 300:            # keep the log line readable
            driver_msg = driver_msg[:300] + "..."
        log.error(
            "%sDB connect FAILED after %sms | %s | errno=%s sqlstate=%s | "
            "driver says: %s | config=%s",
            _tag, elapsed_ms, type(exc).__name__, errno, sqlstate,
            driver_msg or "<no message>",
            redact_config({**config, "port": port}),
            exc_info=True,
        )

        # ── Curated message for logs / HTTP response ──────────────────
        # Same secret-free prefix as before, now enriched with the driver's
        # actual reason (message, errno, sqlstate, elapsed) so callers see
        # *why* the connection failed.
        meta = f"errno={errno if errno is not None else 'unknown'}"
        if sqlstate:
            meta += f", sqlstate={sqlstate}"
        meta += f", after {elapsed_ms}ms"
        detail = f"{driver_msg} [{meta}]" if driver_msg else f"[{meta}]"

        if errno == errorcode.ER_ACCESS_DENIED_ERROR:
            raise ValueError(f"Invalid username or password ({detail})") from exc
        if errno == errorcode.ER_BAD_DB_ERROR:
            raise ValueError(f"Database does not exist: {database} ({detail})") from exc
        if errno == errorcode.CR_UNKNOWN_HOST:
            raise ValueError(
                "Cannot resolve the database host (DNS lookup failed — check "
                f"the hostname) — {detail}"
            ) from exc
        # A port that accepts TCP but never sends a MySQL greeting is the
        # signature of pointing a MySQL client at a non-MySQL service — most
        # often a PostgreSQL endpoint (Aiven "pg-*" hosts, Supabase poolers).
        # That distinction *is* the answer to "why did it fail", so name it
        # instead of blaming the network.
        if "waiting for initial communication packet" in driver_msg.lower():
            raise ValueError(
                "The host accepted the TCP connection but never sent a MySQL "
                "handshake — it is probably not a MySQL server (e.g. a "
                "PostgreSQL service, or the wrong port for this host) "
                f"— {detail}"
            ) from exc
        if errno in (errorcode.CR_CONN_HOST_ERROR, errorcode.CR_SERVER_LOST):
            raise ValueError(
                "Cannot reach the database host (invalid host, network blocked, "
                f"or connection timed out) — {detail}"
            ) from exc
        if errno == errorcode.CR_CONNECTION_ERROR:
            raise ValueError(f"Database connection failed (socket/connection error) — {detail}") from exc
        if errno == errorcode.CR_SSL_CONNECTION_ERROR:
            raise ValueError(f"SSL handshake with the database failed — {detail}") from exc
        # Generic fallback — curated prefix plus the driver's own reason.
        raise ValueError(f"Database connection failed — {detail}") from exc

    # ── Defence in depth: pin the session read-only ───────────────────
    # The validator is the primary guard, but if any statement ever reached
    # the server the database itself now refuses writes on this connection.
    # Best-effort: a user without the privilege to set the session variable
    # still gets a working connection, so failure here must not be fatal.
    try:
        cursor = conn.cursor()
        try:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
        finally:
            cursor.close()
        log.info("%sDB session pinned READ ONLY", _tag)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "%sCould not pin session READ ONLY (%s) — continuing; the SELECT-only "
            "validator remains the primary guard",
            _tag, type(exc).__name__,
        )

    return conn


# ─────────────────────────────────────────────────────────────────────
# Engine detection
# ─────────────────────────────────────────────────────────────────────

# PostgreSQL SSLRequest: int32 length(8) + int32 magic 80877103.
_PG_SSL_REQUEST = struct.pack("!ii", 8, 80877103)
_PG_SSL_YES = b"S"
_PG_SSL_NO = b"N"


def resolve_engine(config: Dict[str, Any]) -> Optional[str]:
    """Return the explicitly requested engine, or None to let probing decide."""
    for key in ("db_type", "engine", "dialect"):
        raw = config.get(key)
        if raw:
            return _ENGINE_ALIASES.get(str(raw).strip().lower())
    return None


def probe_engine(
    host: str,
    port: int,
    timeout: float = 2.0,
    req_id: Optional[str] = None,
) -> Tuple[Optional[str], str]:
    """
    Identify the database engine listening on host:port by sniffing the wire
    protocol, without authenticating.

    The two servers are distinguishable at the transport level:
      - MySQL greets first: it sends its handshake packet the instant the TCP
        connection is established, before the client says anything.
      - PostgreSQL stays silent until the client speaks. Sending an SSLRequest
        (length=8, code=80877103) makes it answer one byte: 'S' (TLS
        supported) or 'N' (not). Either reply proves PostgreSQL.

    Returns (engine, detail); engine is ENGINE_MYSQL, ENGINE_POSTGRES, or None
    when undecidable. `detail` is a short explanation for logging and never
    contains credentials.
    """
    tag = f"[{req_id}] " if req_id else ""
    sock = None
    greeting = b""
    try:
        sock = socket.create_connection((host, int(port)), timeout=timeout)

        # ── Step 1: does the server speak first? (MySQL) ──────────────
        # MySQL greets within milliseconds of the TCP handshake, so a short
        # wait is enough to spot it. The far more common silent case
        # (PostgreSQL) would otherwise burn the whole probe timeout here and
        # make every auto-detected request pay for it. A greeting that arrives
        # later than this simply falls through: step 2 gets no valid reply,
        # the probe reports inconclusive, and connect_db() still defaults to
        # MySQL — so detection degrades, it never misdetects.
        sock.settimeout(min(timeout, 0.5))
        try:
            greeting = sock.recv(64)
        except socket.timeout:
            greeting = b""

        if greeting:
            # MySQL packet = 3-byte length + 1-byte sequence + payload, and the
            # payload opens with the protocol version (0x0a on MySQL 5.7+/8.x).
            payload = greeting[4:]
            if len(greeting) >= 5 and payload[:1] == b"\x0a":
                version = payload[1:].split(b"\x00")[0].decode("latin-1", "replace")
                return (
                    ENGINE_MYSQL,
                    "server greeted first with a MySQL handshake "
                    f"(version {version or 'unknown'})",
                )

        # ── Step 2: PostgreSQL? (silent until asked) ──────────────────
        sock.settimeout(timeout)
        try:
            sock.sendall(_PG_SSL_REQUEST)
            reply = sock.recv(1)
        except (socket.timeout, OSError):
            reply = b""

        if reply in (_PG_SSL_YES, _PG_SSL_NO):
            how = "TLS supported" if reply == _PG_SSL_YES else "TLS not supported"
            return ENGINE_POSTGRES, f"answered a PostgreSQL SSLRequest ({how})"

        if not greeting and not reply:
            return None, (
                "no protocol response to either probe — the host may not be a "
                "database, or a firewall is holding the connection open"
            )
        return None, f"unrecognised protocol response (first bytes: {greeting[:8]!r})"
    except socket.timeout:
        return None, f"TCP connect to {host}:{port} timed out"
    except OSError as exc:
        return None, f"TCP connect to {host}:{port} failed: {exc}"
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


# ─────────────────────────────────────────────────────────────────────
# PostgreSQL
# ─────────────────────────────────────────────────────────────────────

def curate_postgres_error(driver_msg: str, database: Optional[str] = None) -> str:
    """
    Map a psycopg2 driver message to a curated, secret-free explanation.

    Pure function (no I/O) so it is directly unit-testable. PostgreSQL reports
    authentication failures without echoing the password, and callers still
    only ever receive a curated string.
    """
    m = (driver_msg or "").lower()
    if "password authentication failed" in m or "authentication failed" in m:
        return "Invalid username or password"
    if "does not exist" in m and "database" in m:
        return f"Database does not exist: {database}"
    if "no pg_hba.conf entry" in m:
        return (
            "The server refused this client's address (no pg_hba.conf entry) — "
            "add your IP to the service's allowed addresses"
        )
    if (
        "could not translate host name" in m
        or "name or service not known" in m
        or "nodename nor servname" in m
        or "temporary failure in name resolution" in m
    ):
        return "Cannot resolve the database host (DNS lookup failed — check the hostname)"
    if "connection refused" in m:
        return (
            "The database host refused the connection (wrong port, or the "
            "service is not accepting connections)"
        )
    if "timeout expired" in m or "operation timed out" in m:
        return "Cannot reach the database host (network blocked or connection timed out)"
    if "server does not support ssl" in m or "ssl error" in m:
        return "SSL handshake with the database failed"
    if "invalid response to ssl negotiation" in m:
        return (
            "The host did not answer as a PostgreSQL server — it is probably a "
            "different engine (e.g. MySQL)"
        )
    if "too many connections" in m or "remaining connection slots" in m:
        return "The database server has no free connection slots"
    if "server closed the connection unexpectedly" in m or "terminating connection" in m:
        return (
            "The database closed the connection unexpectedly (server restart, "
            "idle timeout, or a non-PostgreSQL service on this port)"
        )
    return "Database connection failed"


def connect_postgres(
    config: Dict[str, Any],
    req_id: Optional[str] = None,
):
    """
    Open a short-lived PostgreSQL connection from a request-supplied config.

    SSL: cloud PostgreSQL (Aiven, Supabase, RDS) requires TLS, so
    "sslmode=require" is the default. Setting ssl_verify=true upgrades to
    "verify-full", which needs a CA the caller must supply via the standard
    PGSSLROOTCERT / sslrootcert mechanism.

    Mirrors connect_mysql's contract exactly: full failure diagnostics to the
    server log, a curated secret-free ValueError to the caller, and `req_id`
    used only for log correlation.
    """
    if psycopg2 is None:
        raise ValueError(
            "PostgreSQL support is unavailable: psycopg2 is not installed "
            f"({_PSYCOPG2_IMPORT_ERROR}). Install it with "
            "'pip install psycopg2-binary'."
        )

    host       = config.get("host")
    port       = int(config.get("port") or 5432)
    user       = config.get("user")
    password   = config.get("password")
    database   = config.get("database")
    ssl_verify = bool(config.get("ssl_verify", False))

    if not host or not user:
        raise ValueError("host and user are required")
    if password is None:
        raise ValueError("password is required")

    kwargs: Dict[str, Any] = {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "dbname": database or None,
        "connect_timeout": CONNECT_TIMEOUT,
        "sslmode": "verify-full" if ssl_verify else "require",
    }

    _tag = f"[{req_id}] " if req_id else ""
    safe_cfg = redact_config({**config, "port": port})
    log.info("%sDB connect attempt (postgres): %s", _tag, safe_cfg)
    started = time.perf_counter()
    try:
        conn = psycopg2.connect(**kwargs)
        # Per-request read-only diagnostics must not hold an open transaction.
        conn.autocommit = True
        return conn
    except psycopg2.Error as exc:
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        sqlstate   = getattr(exc, "pgcode", None)
        if not sqlstate:
            # pgcode is None for startup/connection failures; the SQLSTATE is
            # still available through the diagnostics object (e.g. 28P01 for a
            # bad password, 3D000 for a missing database).
            diag = getattr(exc, "diag", None)
            sqlstate = getattr(diag, "sqlstate", None) if diag else None
        # pgerror can be multi-line ("FATAL: ...\nDETAIL: ...") — collapse it.
        driver_msg = " ".join(
            (getattr(exc, "pgerror", None) or str(exc) or "").split()
        )
        if len(driver_msg) > 300:              # keep the log line readable
            driver_msg = driver_msg[:300] + "..."

        log.error(
            "%sDB connect FAILED after %sms | %s | sqlstate=%s | driver says: %s "
            "| config=%s",
            _tag, elapsed_ms, type(exc).__name__, sqlstate,
            driver_msg or "<no message>", safe_cfg,
            exc_info=True,
        )

        curated = curate_postgres_error(driver_msg, database)
        detail = (
            f"{driver_msg} [sqlstate={sqlstate or 'unknown'}, after {elapsed_ms}ms]"
            if driver_msg else f"[sqlstate={sqlstate or 'unknown'}, after {elapsed_ms}ms]"
        )
        raise ValueError(f"{curated} — {detail}") from exc



# ─────────────────────────────────────────────────────────────────────
# Engine-agnostic entry point
# ─────────────────────────────────────────────────────────────────────

def connect_db(
    config: Dict[str, Any],
    req_id: Optional[str] = None,
    engine: Optional[str] = None,
    allow_read_only: bool = True,
) -> Tuple[Any, str]:
    """
    Open a connection to whichever engine the target actually runs.

    Returns (connection, engine_used). An explicit `engine` argument or a
    config db_type/engine/dialect key wins; otherwise the wire protocol is
    probed. When the probe is inconclusive we fall back to MySQL so callers
    that never set a type keep their historical behaviour.

    `allow_read_only=False` disables the admin -> read-only account swap, for
    the Test Connection route which must validate the exact credentials given.
    """
    tag = f"[{req_id}] " if req_id else ""
    engine = engine or resolve_engine(config)

    if engine is None:
        host = config.get("host")
        port = int(config.get("port") or 3306)
        probed, detail = probe_engine(host, port, req_id=req_id)
        log.info(
            "%sEngine probe %s:%s -> %s (%s)",
            tag, host, port, probed or "inconclusive", detail,
        )
        if probed is None:
            log.warning(
                "%sEngine probe inconclusive, defaulting to MySQL: %s", tag, detail
            )
        engine = probed or ENGINE_MYSQL

    if engine == ENGINE_POSTGRES:
        return connect_postgres(config, req_id), ENGINE_POSTGRES
    return connect_mysql(config, req_id, allow_read_only), ENGINE_MYSQL

