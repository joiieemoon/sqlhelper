# -*- coding: utf-8 -*-
"""
R&D tests for POST /db_optimize_test (direct MySQL connection endpoint).

Run:  python test_db_endpoint.py
Live Aiven tests require DB_HOST / DB_PORT / DB_USER / DB_PASSWORD / DB_NAME
(loaded from backend/.env). Without them, only offline tests run.
"""

import io
import json
import logging
import os
import socket
import unittest
from unittest import mock

import mysql.connector

# The daily query quota is a product limit (5/day/device), but these tests all
# share one TestClient identifier and would exhaust it part-way through the run,
# turning unrelated assertions into 429 failures. Raise it for the test process
# only — must happen BEFORE api/rate_limiter are imported, since the limit is
# read from the environment at import time.
os.environ.setdefault("RATE_LIMIT_MAX", "100000")

from api import app
import api
import db_schema
from fastapi.testclient import TestClient
import db_utils

client = TestClient(app)
ENDPOINT = "/db_optimize_test"
FAKE_PW = "SUPER-SECRET-PASSWORD-123"

def live_cfg():
    return {
        "host": os.getenv("DB_HOST", ""),
        "port": int(os.getenv("DB_PORT", "3306")),
        "user": os.getenv("DB_USER", ""),
        "password": os.getenv("DB_PASSWORD", ""),
        "database": os.getenv("DB_NAME", ""),
    }

def has_live_db():
    return all(live_cfg().values())


class OfflineTests(unittest.TestCase):
    """No real database needed."""

    def test_missing_database_config(self):
        r = client.post(ENDPOINT, json={"query": "SELECT 1"})
        self.assertEqual(r.status_code, 422)  # pydantic required field
        self.assertNotIn(FAKE_PW, r.text)

    def test_missing_required_fields(self):
        r = client.post(ENDPOINT, json={
            "database_config": {"host": "x", "user": "u", "password": "p"},
            "query": "SELECT 1",
        })
        self.assertEqual(r.status_code, 422)  # database missing
        self.assertNotIn(FAKE_PW, r.text)

    def test_invalid_port(self):
        r = client.post(ENDPOINT, json={
            "database_config": {"host": "x", "port": 99999, "user": "u",
                                "password": "p", "database": "d"},
            "query": "SELECT 1",
        })
        self.assertEqual(r.status_code, 422)

    def test_write_query_rejected_before_connect(self):
        with mock.patch.object(api, "connect_mysql",
                               side_effect=AssertionError("must not connect")):
            r = client.post(ENDPOINT, json={
                "database_config": {"host": "x", "user": "u",
                                    "password": "p", "database": "d"},
                "query": "DELETE FROM customers",
            })
        self.assertFalse(r.json()["success"])
        self.assertIn("SELECT", r.json()["error"])

    def test_invalid_sql_rejected(self):
        with mock.patch.object(api, "connect_mysql",
                               side_effect=AssertionError("must not connect")):
            r = client.post(ENDPOINT, json={
                "database_config": {"host": "x", "user": "u",
                                    "password": "p", "database": "d"},
                "query": "SELEC nonsense",
            })
        self.assertFalse(r.json()["success"])


    def test_invalid_host_maps_to_actionable_error(self):
        """
        DNS failure must name the cause and keep driver detail — but never the
        password, and never a raw traceback.

        (The hostname itself is echoed: it was supplied by this same request,
        so it is not a secret, and naming it is what makes the error useful.)
        """
        bad = {"host": "no-such-host.invalid", "port": 3306, "user": "u",
               "password": FAKE_PW, "database": "d"}
        with mock.patch.object(api, "connect_db",
                               side_effect=ValueError(
                                   "Cannot resolve the database host (DNS lookup "
                                   "failed — check the hostname) — Unknown MySQL "
                                   "server host 'no-such-host.invalid' (11001) "
                                   "[errno=2005, sqlstate=HY000, after 3ms]")):
            r = client.post(ENDPOINT, json={"database_config": bad, "query": "SELECT 1"})
        self.assertEqual(r.status_code, 400)
        body = r.text
        self.assertIn("DNS", body)                  # actionable cause named
        self.assertIn("errno=2005", body)           # driver detail preserved
        self.assertNotIn(FAKE_PW, body)             # 7. password not in response
        self.assertNotIn("Traceback", body)         # no internal dump to callers

    def test_password_not_in_logs(self):
        bad = {"host": "h", "user": "u", "password": FAKE_PW, "database": "d"}
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        db_log = logging.getLogger("query_optimizer")  # capture api.py + db_utils records
        db_log.addHandler(handler)
        try:
            with mock.patch.object(api, "connect_db",
                                   side_effect=ValueError("Database connection failed")):
                client.post(ENDPOINT, json={"database_config": bad})
        finally:
            db_log.removeHandler(handler)
        logged = stream.getvalue()
        self.assertNotIn(FAKE_PW, logged)            # 8. password not in logs
        self.assertIn("***", logged)                 # redaction marker present

    def test_connection_closed_after_request(self):
        closed = {"flag": False}

        class FakeCursor:
            with_rows = True
            def execute(self, q): pass
            def fetchall(self): return [(1,)]
            def close(self): pass

        class FakeConn:
            def cursor(self): return FakeCursor()
            def close(self): closed["flag"] = True    # 9. close asserted

        cfg = {"host": "h", "user": "u", "password": FAKE_PW, "database": "d"}
        with mock.patch.object(api, "connect_db",
                               return_value=(FakeConn(), "mysql")):
            r = client.post(ENDPOINT, json={"database_config": cfg, "query": "SELECT 1"})
        self.assertTrue(r.json()["success"])
        self.assertTrue(closed["flag"], "connection.close() must be called")

    def test_redact_config(self):
        safe = db_utils.redact_config({"host": "h", "password": FAKE_PW})
        self.assertEqual(safe["password"], "***")
        self.assertEqual(safe["host"], "h")


class SqlDiffTests(unittest.TestCase):
    """
    Offline tests for api.diff_sql().

    Regression guard: a rewriter that deletes one predicate from a long WHERE
    clause used to be indistinguishable from "nothing changed" — the two
    reindented blobs looked identical and applied_actions counted NO_OPs as
    edits. These tests pin the detection of the real edit.
    """

    # The exact pair returned by /db_optimize_full for the Ahmedabad query.
    ORIGINAL = (
        "SELECT DISTINCT c.name, o.id, o.total_amount, o.status FROM customers c, "
        "orders c_dummy, orders o WHERE c.id = o.customer_id AND "
        "c.id = c_dummy.customer_id AND "
        "LOWER(SUBSTRING(TRIM(CAST(c.city AS CHAR)), 1, 9)) = 'Ahmedabad' AND "
        "LOWER(CONCAT(o.status, '')) = 'completed' AND o.total_amount IS NOT NULL "
        "AND (SELECT COUNT(*) FROM orders o2 WHERE o2.id = o.id) > 0 "
        "GROUP BY c.name, o.id, o.total_amount, o.status, c.city, o.customer_id "
        "ORDER BY (CAST(o.total_amount AS CHAR) * 1) DESC;"
    )
    OPTIMIZED = ORIGINAL.replace(
        "AND (SELECT COUNT(*) FROM orders o2 WHERE o2.id = o.id) > 0 ", ""
    ).rstrip(";")

    def test_detects_removed_predicate(self):
        changed, changes = api.diff_sql(self.ORIGINAL, self.OPTIMIZED)
        self.assertTrue(changed)
        self.assertTrue(
            any("SELECT COUNT(*) FROM orders o2" in c for c in changes),
            f"removal not reported: {changes}",
        )
        self.assertTrue(all(c.startswith("removed:") for c in changes), changes)

    def test_reformat_only_is_not_a_change(self):
        # format_sql_for_display() reindents both sides — that must not count.
        changed, changes = api.diff_sql(
            "SELECT id, name FROM customers WHERE id = 1;",
            "SELECT   id,\n  name\nFROM customers\nWHERE id = 1",
        )
        self.assertFalse(changed)
        self.assertEqual(changes, [])

    def test_trailing_semicolon_and_case_ignored(self):
        changed, _ = api.diff_sql("select 1 from dual;", "SELECT 1 FROM dual")
        self.assertFalse(changed)

    def test_identical_query_reports_no_changes(self):
        changed, changes = api.diff_sql(self.ORIGINAL, self.ORIGINAL)
        self.assertFalse(changed)
        self.assertEqual(changes, [])

    def test_real_rewrite_is_reported(self):
        changed, changes = api.diff_sql(
            "SELECT * FROM orders WHERE LOWER(CONCAT(o.status, '')) = 'x'",
            "SELECT * FROM orders WHERE o.status = 'x'",
        )
        self.assertTrue(changed)
        self.assertTrue(any(c.startswith("replaced:") for c in changes), changes)

    def test_no_op_actions_excluded_from_edit_count(self):
        # FIX 4/5 of the reported case: applied_actions=[1,3,4] where 3 and 4
        # are NO_OP, so only action 1 actually rewrote SQL.
        actions = [
            {"id": 1, "type": "ELIMINATE_SUBQUERY"},
            {"id": 3, "type": "NO_OP"},
            {"id": 4, "type": "NO_OP"},
        ]
        by_id = {a["id"]: a for a in actions}
        applied = [1, 3, 4]
        no_ops = [
            aid for aid in applied
            if (by_id.get(aid) or {}).get("type") in api.NON_SQL_CHANGING_ACTIONS
        ]
        rewrite = [aid for aid in applied if aid not in no_ops]
        self.assertEqual(no_ops, [3, 4])
        self.assertEqual(rewrite, [1])


class ConnectErrorMappingTests(unittest.TestCase):
    """
    Offline tests for db_utils.connect_mysql() error mapping.

    Regression guard: a non-MySQL endpoint (PostgreSQL port) used to surface
    only "Cannot reach the database host ... connection timed out", which sent
    the reader hunting for network/firewall problems when the real cause was an
    engine mismatch.
    """

    CFG = {"host": "pg-host.example", "port": 21378, "user": "u",
           "password": FAKE_PW, "database": "defaultdb", "ssl_verify": False}

    def _raise(self, exc):
        with mock.patch.object(db_utils.mysql.connector, "connect",
                               side_effect=exc):
            return db_utils.connect_mysql(dict(self.CFG))

    def test_non_mysql_endpoint_is_named(self):
        exc = mysql.connector.errors.OperationalError(
            msg="Lost connection to MySQL server at 'waiting for initial "
                "communication packet', system error: 10060",
            errno=2013,
            sqlstate="HY000",
        )
        for attr, expected in (("errno", 2013), ("sqlstate", "HY000")):
            self.assertEqual(getattr(exc, attr), expected)  # guard the fixture
        with self.assertRaises(ValueError) as ctx:
            self._raise(exc)
        msg = str(ctx.exception)
        self.assertIn("not a MySQL server", msg)
        self.assertIn("PostgreSQL", msg)          # actionable hint
        self.assertIn("errno=2013", msg)          # driver detail preserved
        self.assertNotIn(FAKE_PW, msg)            # never leak the password

    def test_dns_failure_is_named(self):
        exc = mysql.connector.errors.DatabaseError(
            msg="Unknown MySQL server host 'nope.invalid' (11001)", errno=2005)
        self.assertEqual(exc.errno, 2005)
        with self.assertRaises(ValueError) as ctx:
            self._raise(exc)
        self.assertIn("DNS", str(ctx.exception))
        self.assertNotIn(FAKE_PW, str(ctx.exception))

    def test_access_denied_is_named(self):
        exc = mysql.connector.errors.ProgrammingError(
            msg="Access denied for user 'u'@'x' (using password: YES)",
            errno=1045, sqlstate="28000")
        self.assertEqual(exc.errno, 1045)
        with self.assertRaises(ValueError) as ctx:
            self._raise(exc)
        msg = str(ctx.exception)
        self.assertIn("Invalid username or password", msg)
        self.assertIn("errno=1045", msg)
        self.assertNotIn(FAKE_PW, msg)


class _FakeSock:
    """Socket double for probe_engine(): scripted recv() replies."""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.sent = b""
        self.closed = False

    def settimeout(self, _seconds):
        pass

    def sendall(self, data):
        self.sent += data

    def recv(self, _n):
        if self._chunks:
            chunk = self._chunks.pop(0)
            if isinstance(chunk, Exception):
                raise chunk
            return chunk
        raise socket.timeout("no data")

    def close(self):
        self.closed = True


class EngineDetectionTests(unittest.TestCase):
    """
    Offline tests for engine detection / dispatch (no network).

    Guards the behaviour that made the original bug confusing: a MySQL client
    pointed at PostgreSQL silently hung for ~10s and reported a generic
    connection timeout. probe_engine() must name the engine instead, and
    psycopg2 must stay optional so the module still imports without it.
    """

    PG_CFG = {
        "host": "pg.example", "port": 21378, "user": "u",
        "password": FAKE_PW, "database": "defaultdb", "ssl_verify": False,
    }

    def test_resolve_engine_aliases(self):
        for raw in ("postgres", "PostgreSQL", " pg "):
            self.assertEqual(db_utils.resolve_engine({"db_type": raw}),
                             db_utils.ENGINE_POSTGRES, raw)
        for raw in ("mysql", "MySQL", "mariadb"):
            self.assertEqual(db_utils.resolve_engine({"db_type": raw}),
                             db_utils.ENGINE_MYSQL, raw)
        # Accepted from any of the three keys, and None when unspecified.
        self.assertEqual(db_utils.resolve_engine({"engine": "postgres"}),
                         db_utils.ENGINE_POSTGRES)
        self.assertEqual(db_utils.resolve_engine({"dialect": "mysql"}),
                         db_utils.ENGINE_MYSQL)
        self.assertIsNone(db_utils.resolve_engine({}))
        self.assertIsNone(db_utils.resolve_engine({"db_type": ""}))

    def test_probe_detects_mysql_from_greeting(self):
        # MySQL greets first: proto 0x0a then "8.4.8\0" inside a 4-byte header.
        sock = _FakeSock([b"\x05\x00\x00\x00\x0a8.4.8\x00abc"])
        with mock.patch.object(db_utils.socket, "create_connection",
                               return_value=sock):
            engine, detail = db_utils.probe_engine("h", 3306, timeout=1)
        self.assertEqual(engine, db_utils.ENGINE_MYSQL)
        self.assertIn("8.4.8", detail)
        self.assertTrue(sock.closed)              # never leaks a socket

    def test_probe_detects_postgres_ssl_yes(self):
        # Silent until asked, then answers 'S' to the SSLRequest.
        sock = _FakeSock([socket.timeout(), b"S"])
        with mock.patch.object(db_utils.socket, "create_connection",
                               return_value=sock):
            engine, detail = db_utils.probe_engine("h", 5432, timeout=1)
        self.assertEqual(engine, db_utils.ENGINE_POSTGRES)
        self.assertEqual(sock.sent, db_utils._PG_SSL_REQUEST)
        self.assertIn("SSLRequest", detail)
        self.assertTrue(sock.closed)

    def test_probe_detects_postgres_ssl_no(self):
        sock = _FakeSock([socket.timeout(), b"N"])
        with mock.patch.object(db_utils.socket, "create_connection",
                               return_value=sock):
            engine, _ = db_utils.probe_engine("h", 5432, timeout=1)
        self.assertEqual(engine, db_utils.ENGINE_POSTGRES)

    def test_probe_inconclusive_when_silent(self):
        sock = _FakeSock([socket.timeout(), socket.timeout()])
        with mock.patch.object(db_utils.socket, "create_connection",
                               return_value=sock):
            engine, detail = db_utils.probe_engine("h", 1234, timeout=1)
        self.assertIsNone(engine)
        self.assertIn("no protocol response", detail)

    def test_probe_reports_tcp_failure_without_raising(self):
        engine, detail = db_utils.probe_engine("nope.invalid", 3306, timeout=1)
        self.assertIsNone(engine)
        self.assertIn("failed", detail)

    # ── connect_db dispatch ─────────────────────────────────────────────

    def _dispatch(self, probed):
        with mock.patch.object(db_utils, "probe_engine",
                               return_value=(probed, "why")), \
             mock.patch.object(db_utils, "connect_mysql",
                               return_value="MYSQL_CONN") as m_mysql, \
             mock.patch.object(db_utils, "connect_postgres",
                               return_value="PG_CONN") as m_pg:
            conn, engine = db_utils.connect_db(dict(self.PG_CFG), "rid")
        return conn, engine, m_mysql, m_pg

    def test_connect_db_uses_postgres_when_probed_postgres(self):
        conn, engine, m_mysql, m_pg = self._dispatch(db_utils.ENGINE_POSTGRES)
        self.assertEqual(engine, db_utils.ENGINE_POSTGRES)
        self.assertEqual(conn, "PG_CONN")
        m_pg.assert_called_once()
        m_mysql.assert_not_called()

    def test_connect_db_defaults_to_mysql_when_inconclusive(self):
        # Historical behaviour: callers that never set a type still get MySQL.
        conn, engine, m_mysql, m_pg = self._dispatch(None)
        self.assertEqual(engine, db_utils.ENGINE_MYSQL)
        self.assertEqual(conn, "MYSQL_CONN")
        m_mysql.assert_called_once()
        m_pg.assert_not_called()

    def test_connect_db_skips_probe_when_db_type_given(self):
        cfg = dict(self.PG_CFG)
        cfg["db_type"] = "postgres"
        with mock.patch.object(
                db_utils, "probe_engine",
                side_effect=AssertionError("must not probe")) as m_probe, \
             mock.patch.object(db_utils, "connect_postgres",
                               return_value="PG_CONN"):
            conn, engine = db_utils.connect_db(cfg, "rid")
        self.assertEqual(engine, db_utils.ENGINE_POSTGRES)
        m_probe.assert_not_called()

    def test_missing_psycopg2_gives_installable_message(self):
        with mock.patch.object(db_utils, "psycopg2", None):
            with self.assertRaises(ValueError) as ctx:
                db_utils.connect_postgres(dict(self.PG_CFG))
        msg = str(ctx.exception)
        self.assertIn("psycopg2", msg)
        self.assertIn("pip install psycopg2-binary", msg)
        self.assertNotIn(FAKE_PW, msg)


class PostgresErrorCurationTests(unittest.TestCase):
    """
    Offline tests for PostgreSQL error mapping and dialect validation.

    The HTTP/log message a caller sees must always name the real cause and
    must never contain the password.
    """

    PG_CFG = {
        "host": "pg.example", "port": 21378, "user": "u",
        "password": FAKE_PW, "database": "defaultdb", "ssl_verify": False,
    }

    def test_curate_postgres_error_mappings(self):
        cases = [
            ('password authentication failed for user "avnadmin"',
             "Invalid username or password"),
            ('FATAL:  database "nope" does not exist',
             "Database does not exist"),
            ('no pg_hba.conf entry for host "1.2.3.4"', "pg_hba.conf"),
            ('could not translate host name "bad.host" to address',
             "DNS lookup failed"),
            ("Connection refused (111)", "refused the connection"),
            ("timeout expired", "timed out"),
            ("server does not support SSL", "SSL"),
            ("remaining connection slots are reserved",
             "free connection slots"),
            ("server closed the connection unexpectedly", "unexpectedly"),
            ("invalid response to ssl negotiation", "different engine"),
            ("something entirely novel", "Database connection failed"),
        ]
        for driver_msg, expected in cases:
            out = db_utils.curate_postgres_error(driver_msg, "defaultdb")
            self.assertIn(expected, out, driver_msg)
            self.assertNotIn(FAKE_PW, out)

    def test_connect_postgres_never_surfaces_password(self):
        class _FakeError(Exception):
            pgcode = "28P01"
            pgerror = (
                'FATAL:  password authentication failed for user "u"\n'
                "DETAIL:  Password does not match"
            )

        class _FakePsycopg2:
            Error = _FakeError

            @staticmethod
            def connect(**_kw):
                raise _FakeError()

        with mock.patch.object(db_utils, "psycopg2", _FakePsycopg2):
            with self.assertRaises(ValueError) as ctx:
                db_utils.connect_postgres(dict(self.PG_CFG))
        msg = str(ctx.exception)
        self.assertIn("Invalid username or password", msg)
        self.assertIn("after", msg)              # elapsed detail preserved
        self.assertIn("Password does not match", msg)   # driver reason kept
        self.assertNotIn(FAKE_PW, msg)

    def test_pg_cast_syntax_accepted_when_engine_known(self):
        q = "SELECT id::text FROM customers"
        self.assertEqual(api.validate_for_engine(q, "postgres"), "FINE")
        # Engine unknown (probe not run yet): accepted if either dialect parses.
        self.assertEqual(api.validate_for_engine(q, None), "FINE")

    def test_write_rejected_in_both_dialects(self):
        for dialect in ("mysql", "postgres"):
            self.assertNotEqual(
                api.validate_for_engine("DELETE FROM customers", dialect),
                "FINE", dialect)


class SchemaExtractTests(unittest.TestCase):
    """Offline tests for POST /db_schema_extract (Step 1)."""

    SCHEMA_EP = "/db_schema_extract"
    GOOD_CFG = {"host": "h", "user": "u", "password": "p", "database": "d"}

    class _FakeCursor:
        def __init__(self):
            self.calls = []
        def execute(self, q, params=None):
            self.calls.append(q.strip()[:40])
            self.last_q = q
        def fetchone(self):
            if "TABLE_ROWS" in self.last_q: return (250000,)
            if "TABLE STATUS" in self.last_q: return ("customers","InnoDB",10,"Dynamic",250000,180,45000000,0,3000000,100000)
            return None
        def fetchall(self):
            if "INFORMATION_SCHEMA.COLUMNS" in self.last_q:
                return [("id","int","NO"), ("name","varchar(100)","NO"),
                        ("country","varchar(50)","NO"), ("status","varchar(20)","NO")]
            if "INFORMATION_SCHEMA.STATISTICS" in self.last_q:
                return [("PRIMARY","id",0,250000,"BTREE",1,None,"NO","YES")]
            return []
        def close(self): pass

    class _FakeConn:
        def cursor(self): return SchemaExtractTests._FakeCursor()
        def close(self): pass

    def test_rejects_write_query_without_connecting(self):
        with mock.patch.object(api, "connect_db",
                               side_effect=AssertionError("must not connect")):
            r = client.post(self.SCHEMA_EP, json={
                "database_config": self.GOOD_CFG,
                "query": "DELETE FROM customers"})
        self.assertFalse(r.json()["success"])
        self.assertIn("SELECT", r.json()["error"])

    def test_returns_pipeline_shaped_schema(self):
        with mock.patch.object(api, "connect_db",
                               return_value=(self._FakeConn(), "mysql")):
            r = client.post(self.SCHEMA_EP, json={
                "database_config": self.GOOD_CFG,
                "query": "SELECT id, name FROM customers WHERE country = 'USA'"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["query_tables"], ["customers"])
        tbl = body["schema"]["customers"]
        self.assertEqual(tbl["row_count"], 250000)
        col_names = [c["name"] for c in tbl["columns"]]
        self.assertIn("country", col_names)
        self.assertIn("filter", tbl["column_roles"]["country"])
        idx = tbl["indexes"][0]
        self.assertEqual(idx["name"], "PRIMARY")
        self.assertTrue(idx["unique"])

    def test_password_never_in_schema_response(self):
        cfg = dict(self.GOOD_CFG); cfg["password"] = FAKE_PW
        with mock.patch.object(api, "connect_db",
                               return_value=(self._FakeConn(), "mysql")):
            r = client.post(self.SCHEMA_EP, json={"database_config": cfg,
                                                  "query": "SELECT 1 FROM customers"})
        self.assertNotIn(FAKE_PW, r.text)

    def test_multi_table_query_lists_both(self):
        with mock.patch.object(api, "connect_db",
                               return_value=(self._FakeConn(), "mysql")):
            r = client.post(self.SCHEMA_EP, json={
                "database_config": self.GOOD_CFG,
                "query": "SELECT o.id FROM orders o JOIN customers c ON o.customer_id = c.id"})
        body = r.json()
        self.assertEqual(sorted(body["query_tables"]), ["customers", "orders"])
        self.assertEqual(len(body["joins"]), 1)

class SchemaResolutionTests(unittest.TestCase):
    """
    A table that exists but under a differently-cased schema (or a different
    name spelling) used to come back as an empty-but-successful schema with no
    explanation -- indistinguishable from a table that simply has no columns.
    These tests pin the explanation.

    The modelled server is the real one this was found on: tables in 'Chinook',
    a second empty schema 'chinook', and lower_case_table_names=0.
    """

    SCHEMA_EP = "/db_schema_extract"
    GOOD_CFG = {"host": "h", "user": "u", "password": "p", "database": "chinook"}

    class _Cursor(SchemaExtractTests._FakeCursor):
        """Catalog fake: tables live in `tables`, the connection defaults to `current`."""

        def __init__(self, current, tables, lctn=0):
            super().__init__()
            self.current = current
            self.tables = tables          # {(schema, table)}
            self.lctn = lctn
            self.last_params = None

        def execute(self, q, params=None):
            super().execute(q, params)
            self.last_params = params

        def fetchone(self):
            if "SELECT DATABASE()" in self.last_q:
                return (self.current,)
            if "@@lower_case_table_names" in self.last_q:
                return (self.lctn,)
            return super().fetchone()

        def fetchall(self):
            if ("INFORMATION_SCHEMA.TABLES" in self.last_q
                    and "LOWER(TABLE_NAME)" in self.last_q):
                wanted = (self.last_params[0] or "").lower()
                return sorted((s, t) for (s, t) in self.tables
                              if t.lower() == wanted)
            return super().fetchall()

    def _conn(self, current, tables, lctn=0):
        outer = self

        class _Conn:
            def cursor(self):
                return outer._Cursor(current, tables, lctn)

            def close(self):
                pass

        return _Conn()

    # -- resolver unit tests -------------------------------------------------

    def test_table_in_current_schema_is_resolved(self):
        cur = self._Cursor("chinook", {("chinook", "customer")})
        unresolved, resolved = db_schema.resolve_query_tables(cur, ["customer"])
        self.assertEqual(resolved, ["customer"])
        self.assertEqual(unresolved, {})

    def test_table_in_differently_cased_schema_is_reported(self):
        """The real Aiven case: tables in 'Chinook', connected to 'chinook'."""
        cur = self._Cursor("chinook", {("Chinook", "Customer")})
        unresolved, resolved = db_schema.resolve_query_tables(cur, ["Customer"])
        self.assertEqual(resolved, [])
        self.assertIn("Chinook", unresolved["Customer"])
        self.assertIn("chinook", unresolved["Customer"])

    def test_table_name_spelling_mismatch_is_reported(self):
        cur = self._Cursor("shop", {("shop", "orders")})
        unresolved, _ = db_schema.resolve_query_tables(cur, ["Orders"])
        self.assertIn("orders", unresolved["Orders"])

    def test_absent_table_is_reported(self):
        cur = self._Cursor("shop", set())
        unresolved, resolved = db_schema.resolve_query_tables(cur, ["ghost"])
        self.assertEqual(resolved, [])
        self.assertIn("not found", unresolved["ghost"])

    def test_catalog_failure_is_reported_not_raised(self):
        class _Broken:
            def execute(self, q, params=None):
                raise RuntimeError("catalog unavailable")

            def fetchone(self):
                return None

            def fetchall(self):
                return []

            def close(self):
                pass

        unresolved, resolved = db_schema.resolve_query_tables(_Broken(), ["t"])
        self.assertEqual(resolved, [])
        self.assertIn("Could not verify", unresolved["t"])

    def test_spelling_difference_accepted_on_case_insensitive_server(self):
        """lower_case_table_names>=1: the server folds names, so 'Orders' is legal."""
        cur = self._Cursor("shop", {("shop", "orders")}, lctn=1)
        unresolved, resolved = db_schema.resolve_query_tables(cur, ["Orders"])
        self.assertEqual(resolved, ["Orders"])
        self.assertEqual(unresolved, {})

    def test_postgres_spelling_difference_is_not_flagged(self):
        """PostgreSQL folds unquoted identifiers, so 'Orders' resolves to orders."""

        class _PgCursor:
            def __init__(self):
                self.last_q = ""
                self.last_params = None

            def execute(self, q, params=None):
                self.last_q = q
                self.last_params = params

            def fetchone(self):
                if "current_schema()" in self.last_q:
                    return ("public",)
                return None

            def fetchall(self):
                if "information_schema.tables" in self.last_q:
                    return [("public", "orders")]
                return []

            def close(self):
                pass

        unresolved, resolved = db_schema.resolve_query_tables(
            _PgCursor(), ["Orders"], engine="postgres")
        self.assertEqual(resolved, ["Orders"])
        self.assertEqual(unresolved, {})

    # -- endpoint-level tests ------------------------------------------------

    def test_endpoint_warns_when_table_not_in_connected_schema(self):
        conn = self._conn("chinook", {("Chinook", "Customer")})
        with mock.patch.object(api, "connect_db", return_value=(conn, "mysql")):
            r = client.post(self.SCHEMA_EP, json={
                "database_config": self.GOOD_CFG,
                "query": "SELECT COUNT(*) FROM Customer"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["query_tables"], ["Customer"])
        self.assertEqual(body["resolved_tables"], [])
        self.assertIn("Customer", body["unresolved_tables"])
        self.assertTrue(body["schema_warnings"])
        self.assertIn("Chinook", body["schema_warnings"][0])

    def test_endpoint_reports_no_warning_when_schema_matches(self):
        conn = self._conn("chinook", {("chinook", "customers")})
        with mock.patch.object(api, "connect_db", return_value=(conn, "mysql")):
            r = client.post(self.SCHEMA_EP, json={
                "database_config": self.GOOD_CFG,
                "query": "SELECT id, name FROM customers"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["resolved_tables"], ["customers"])
        self.assertEqual(body["unresolved_tables"], {})
        self.assertEqual(body["schema_warnings"], [])




@unittest.skipUnless(has_live_db(), "live credentials not set (DB_* env vars)")
class LiveAivenTests(unittest.TestCase):
    """Real connection tests â€” require DB_* env vars in backend/.env."""

    def test_valid_connection_select_1(self):
        r = client.post(ENDPOINT, json={"database_config": live_cfg(), "query": "SELECT 1"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["rows"], [["1"]])

    def test_user_select_executed(self):
        r = client.post(ENDPOINT, json={"database_config": live_cfg(), "query": "SELECT DATABASE()"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["rows"][0][0], live_cfg()["database"])

    def test_invalid_credentials(self):
        cfg = live_cfg(); cfg["password"] = "definitely-wrong-password"
        r = client.post(ENDPOINT, json={"database_config": cfg})
        self.assertEqual(r.status_code, 400)
        self.assertIn("password", r.json()["error"].lower())
        self.assertNotIn("definitely-wrong-password", r.text)

    def test_password_never_in_live_response(self):
        r = client.post(ENDPOINT, json={"database_config": live_cfg()})
        self.assertNotIn(live_cfg()["password"], r.text)


class JoinMetadataTests(unittest.TestCase):
    """
    Cross JOIN / comma-style joins must be reported in query metadata.

    Regression guard: only `JOIN ... ON` was read, so a query joining via
    `CROSS JOIN` (or `FROM a, b`) reported joins=[] and the joined column
    never received its "join" role — an issue-finder asked for scope="joins"
    was then told the query contains no join at all.
    """

    def test_cross_join_is_reported(self):
        meta = db_schema.extract_query_metadata(
            "SELECT c1.id FROM opt_customers c1 CROSS JOIN opt_customers c2 "
            "WHERE c1.city = c2.city", "postgres")
        self.assertEqual(len(meta["joins"]), 1, meta["joins"])
        self.assertEqual(meta["joins"][0],
                         {"left": "opt_customers.city",
                          "right": "opt_customers.city"})
        self.assertIn("city", meta["join"].get("opt_customers", set()))

    def test_comma_join_is_reported(self):
        meta = db_schema.extract_query_metadata(
            "SELECT a.id FROM a, b WHERE a.x = b.x", "mysql")
        self.assertEqual(len(meta["joins"]), 1, meta["joins"])
        self.assertEqual(meta["joins"][0], {"left": "a.x", "right": "b.x"})

    def test_explicit_on_unchanged(self):
        meta = db_schema.extract_query_metadata(
            "SELECT c1.id FROM t c1 JOIN t c2 ON c1.city = c2.city", "mysql")
        self.assertEqual(len(meta["joins"]), 1)
        self.assertEqual(meta["joins"][0],
                         {"left": "t.city", "right": "t.city"})

    def test_constant_predicate_stays_a_filter(self):
        # `t.a = 5` is a filter, never a join — even beside a cross join.
        meta = db_schema.extract_query_metadata(
            "SELECT a.id FROM a, b WHERE a.x = b.x AND a.id = 5", "mysql")
        self.assertEqual(len(meta["joins"]), 1)
        self.assertNotIn("id", meta["join"].get("a", set()))
        self.assertIn("id", meta["where"].get("a", set()))

    def test_true_cartesian_product_reports_no_predicate(self):
        meta = db_schema.extract_query_metadata(
            "SELECT a.id FROM a CROSS JOIN b", "mysql")
        self.assertEqual(meta["joins"], [])   # nothing to report, not an error

    def test_unqualified_equality_is_not_called_a_join(self):
        meta = db_schema.extract_query_metadata(
            "SELECT a.id FROM a, b WHERE x = b.x", "mysql")
        self.assertEqual(meta["joins"], [])   # too ambiguous to assert


class StaticIssueTests(unittest.TestCase):
    """detect_static_issues() — deterministic, LLM-independent findings."""

    def test_constant_order_by_subquery_is_flagged(self):
        # The exact query that previously came back as "no optimizations".
        out = db_schema.detect_static_issues(
            "SELECT c1.id, c1.name, c1.city FROM opt_customers c1 "
            "CROSS JOIN opt_customers c2 WHERE c1.city::text = c2.city::text "
            "ORDER BY (SELECT RANDOM());", "postgres")
        self.assertEqual(len(out), 1, out)
        f = out[0]
        self.assertEqual(f["rule"], "order by")
        self.assertEqual(f["scope"], "sql_optimization")
        self.assertEqual(f["execution_time_cost"], "SORT_COST")
        self.assertIn("ORDER BY", f["title"])
        self.assertIn("constant", f["evidence"].lower())
        for key in ("title", "location", "impact", "root_cause", "evidence"):
            self.assertIn(key, f)

    def test_row_level_random_is_not_flagged(self):
        # `ORDER BY RANDOM()` is a deliberate shuffle, not dead code.
        self.assertEqual(db_schema.detect_static_issues(
            "SELECT id FROM t ORDER BY RANDOM()", "mysql"), [])

    def test_correlated_subquery_is_not_flagged(self):
        # Per-row key — legitimately orders the result.
        self.assertEqual(db_schema.detect_static_issues(
            "SELECT c.id FROM customers c ORDER BY "
            "(SELECT COUNT(*) FROM orders o WHERE o.customer_id = c.id)",
            "mysql"), [])

    def test_plain_order_by_is_not_flagged(self):
        self.assertEqual(db_schema.detect_static_issues(
            "SELECT id FROM t ORDER BY id DESC", "mysql"), [])

    def test_uncorrelated_aggregate_subquery_is_flagged(self):
        out = db_schema.detect_static_issues(
            "SELECT id FROM t ORDER BY (SELECT MAX(x) FROM stats)", "mysql")
        self.assertEqual(len(out), 1, out)

    def test_location_quotes_the_query_verbatim(self):
        # sqlglot would render postgres RANDOM() as MySQL's RAND() and append
        # NULLS LAST — neither appears in the query the user actually sent.
        out = db_schema.detect_static_issues(
            "SELECT c1.id FROM opt_customers c1 CROSS JOIN opt_customers c2 "
            "WHERE c1.city::text = c2.city::text "
            "ORDER BY (SELECT RANDOM());", "postgres")
        self.assertEqual(len(out), 1, out)
        loc = out[0]["location"]
        self.assertEqual(loc, "ORDER BY (SELECT RANDOM())")
        self.assertNotIn("RAND()", loc)
        self.assertNotIn("NULLS LAST", loc)

    def test_location_stops_before_limit(self):
        out = db_schema.detect_static_issues(
            "SELECT id FROM t ORDER BY (SELECT MAX(x) FROM s) LIMIT 10", "mysql")
        self.assertEqual(len(out), 1, out)
        self.assertEqual(out[0]["location"],
                         "ORDER BY (SELECT MAX(x) FROM s)")

    def test_unparsable_query_returns_no_findings(self):
        self.assertEqual(db_schema.detect_static_issues(
            "SELEC !!! not sql", "mysql"), [])


class StaticFindingPipelineTests(unittest.TestCase):
    """
    static_findings must reach the HTTP response, and an empty issue list must
    skip the planner and rewriter instead of spending two LLM calls on them.
    """

    QUERY = ("SELECT c1.id, c1.name, c1.city FROM opt_customers c1 "
             "CROSS JOIN opt_customers c2 WHERE c1.city::text = c2.city::text "
             "ORDER BY (SELECT RANDOM());")

    CFG = {"host": "h", "port": 5432, "user": "u", "password": FAKE_PW,
           "database": "d", "ssl_verify": False}

    def _post(self, query, model_issues, allow_planner=False):
        """
        allow_planner=False asserts the planner/rewriter are never reached —
        that is the short-circuit under test. Set it True for cases where the
        model did report an issue, so the pipeline legitimately continues.
        """
        if allow_planner:
            planner = mock.AsyncMock(return_value={
                "actions": [], "execution_order": []})
            rewriter = mock.AsyncMock(return_value={
                "optimized_query": query, "ddl_statements": [],
                "summary": [], "applied_actions": [],
                "skipped_actions": [], "warnings": []})
        else:
            planner = mock.Mock(
                side_effect=AssertionError("planner must not run"))
            rewriter = mock.Mock(
                side_effect=AssertionError("rewriter must not run"))

        # The concurrency limiter is created in the FastAPI lifespan, which
        # TestClient never runs — stand in for both sides of it below.
        with mock.patch.object(api, "connect_db",
                               return_value=(mock.Mock(), "postgres")), \
             mock.patch.object(api, "_extract_schema",
                               return_value=({}, {"tables": set()})), \
             mock.patch.object(api, "_run_diagnostics_pipeline",
                               return_value={"explain": None, "warnings": {},
                                             "status": {}}), \
             mock.patch.object(api, "issue_finder_llm",
                               mock.AsyncMock(return_value={
                                   "issues": model_issues})), \
             mock.patch.object(api, "acquire_with_timeout",
                               mock.AsyncMock(return_value=True)), \
             mock.patch.object(api, "_semaphore", mock.Mock()), \
             mock.patch.object(api, "planner_llm", planner), \
             mock.patch.object(api, "rewriter_llm", rewriter):
            self.last_planner = planner
            return client.post("/db_optimize_full", json={
                "database_config": self.CFG,
                "query": query,
                "scope": ["joins", "indexes", "functions"],
                "custom_request": "",
            })

    def test_static_finding_reaches_response_and_drives_planner(self):
        # Model finds nothing, the rule does -> the finding must surface AND
        # be merged into issues so the planner actually works on it.
        r = self._post(self.QUERY, [], allow_planner=True)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["success"])
        findings = body["static_findings"]
        self.assertEqual(len(findings), 1, findings)
        self.assertIn("ORDER BY", findings[0]["title"])
        self.assertNotIn("rule", findings[0])          # internal key stripped
        self.assertNotIn(FAKE_PW, r.text)
        self.assertEqual(body["actions"], [])
        self.last_planner.assert_awaited_once()

    def test_model_duplicate_is_not_merged_but_still_reported(self):
        # Model already flagged it -> not merged again, yet still surfaced.
        r = self._post(self.QUERY, [{
            "id": 1, "scope": "sql_optimization",
            "title": "ORDER BY is dead code", "location": "ORDER BY",
            "impact": "LOW", "execution_time_cost": "SORT_COST",
            "root_cause": "constant", "evidence": "plan",
        }], allow_planner=True)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(r.json()["static_findings"]), 1)

    def test_clean_query_short_circuits_with_empty_findings(self):
        r = self._post("SELECT 1", [])
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["static_findings"], [])
        self.assertEqual(body["actions"], [])
        self.assertEqual(body["summary"],
                         ["No optimizations were applied to the query."])

class DbOptimizeFullSchemaWarningTests(unittest.TestCase):
    """
    A table living in a differently-cased schema must be reported as such -
    both to the caller and to the analyst. From the schema dict alone it is
    indistinguishable from a table whose columns are simply all absent, and
    the response used to say "missing column(s)" for every column the query
    touched, which points the optimisation pipeline at the wrong problem.
    """

    QUERY = "SELECT CustomerId, Country FROM Customer WHERE Country = 'USA'"

    CFG = {"host": "h", "port": 3306, "user": "u", "password": FAKE_PW,
           "database": "chinook", "ssl_verify": False}

    META = {
        "tables": {"Customer"},
        "where": {"Customer": {"Country"}},
        "join": {},
        "order": {},
        "group": {},
        "select": {"Customer": {"CustomerId", "Country"}},
    }

    @classmethod
    def _conn(cls, current, tables, lctn=0):
        outer = SchemaResolutionTests

        class _Conn:
            def cursor(self):
                return outer._Cursor(current, tables, lctn)

            def close(self):
                pass

        return _Conn()

    def _post(self, conn):
        with mock.patch.object(api, "connect_db",
                               return_value=(conn, "mysql")), \
             mock.patch.object(api, "_extract_schema",
                               return_value=({}, self.META)), \
             mock.patch.object(api, "_run_diagnostics_pipeline",
                               return_value={"explain": None, "warnings": {},
                                             "status": {}}), \
             mock.patch.object(api, "issue_finder_llm",
                               mock.AsyncMock(return_value={"issues": []})), \
             mock.patch.object(api, "acquire_with_timeout",
                               mock.AsyncMock(return_value=True)), \
             mock.patch.object(api, "_semaphore", mock.Mock()), \
             mock.patch.object(api, "planner_llm",
                               mock.Mock(side_effect=AssertionError(
                                   "planner must not run"))), \
             mock.patch.object(api, "rewriter_llm",
                               mock.Mock(side_effect=AssertionError(
                                   "rewriter must not run"))):
            return client.post("/db_optimize_full", json={
                "database_config": self.CFG,
                "query": self.QUERY,
                "scope": ["joins", "indexes"],
                "custom_request": "",
            })

    def test_wrong_schema_is_reported_as_schema_problem(self):
        r = self._post(self._conn("chinook", {("Chinook", "Customer")}))
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["success"])
        warnings = body["schema_warnings"]
        self.assertTrue(warnings, body)
        self.assertIn("Chinook", warnings[0])
        # Must not be misreported as columns the query got wrong.
        self.assertNotIn("missing column", warnings[0])
        self.assertNotIn(FAKE_PW, r.text)

    def test_resolvable_table_still_reports_genuine_missing_column(self):
        r = self._post(self._conn("chinook", {("chinook", "Customer")}))
        self.assertEqual(r.status_code, 200, r.text)
        warnings = r.json()["schema_warnings"]
        self.assertTrue(any("missing column" in w for w in warnings), warnings)




class OptimizedQueryExplainTests(unittest.TestCase):
    """
    `explain` is the plan of the ORIGINAL query only, so `explain_optimized`
    carries the plan of the REWRITTEN query. It is the only evidence that the
    rewrite actually changed how the engine executes the statement.

    Two rules matter and are easy to regress:
      - it is collected only when the SQL really changed, and
      - a failed re-plan never fails an otherwise successful optimization.
    """

    QUERY = "SELECT c1.id FROM opt_customers c1 ORDER BY c1.city;"

    CFG = {"host": "h", "port": 3306, "user": "u", "password": FAKE_PW,
           "database": "d", "ssl_verify": False}

    REWRITTEN = "SELECT c1.id FROM opt_customers c1 ORDER BY c1.id;"

    def _post(self, optimized_query, explain_side_effect=None,
              original_explain=None):
        """
        explain_side_effect raises to simulate a driver-level failure that
        get_explain_analyze() does not already absorb.
        """
        rewriter = mock.AsyncMock(return_value={
            "optimized_query": optimized_query, "ddl_statements": [],
            "summary": [], "applied_actions": [1],
            "skipped_actions": [], "warnings": []})
        planner = mock.AsyncMock(return_value={
            "actions": [{"id": 1, "type": "SQL_OPTIMIZATION", "priority": 1,
                         "reason": "sort on an indexed column"}],
            "execution_order": [1]})

        # The pipeline short-circuits before the planner/rewriter when no issue
        # is found, which would leave optimized_query == query and make
        # query_changed False for reasons unrelated to this test. Report one so
        # the rewriter genuinely runs.
        issue_finder = mock.AsyncMock(return_value={"issues": [{
            "id": 1, "scope": "sql_optimization",
            "title": "Filesort on a non-indexed column", "location": "ORDER BY",
            "impact": "HIGH", "execution_time_cost": "SORT_COST",
            "root_cause": "sort", "evidence": "plan",
        }]})

        explain_mock = mock.Mock(return_value=original_explain)
        if explain_side_effect is not None:
            explain_mock.side_effect = explain_side_effect

        with mock.patch.object(api, "connect_db",
                               return_value=(mock.Mock(), "mysql")), \
             mock.patch.object(api, "_extract_schema",
                               return_value=({}, {"tables": set()})), \
             mock.patch.object(api, "_run_diagnostics_pipeline",
                               return_value={"explain": original_explain,
                                             "warnings": {}, "status": {}}), \
             mock.patch.object(api, "_get_explain_analyze", explain_mock), \
             mock.patch.object(api, "issue_finder_llm", issue_finder), \
             mock.patch.object(api, "acquire_with_timeout",
                               mock.AsyncMock(return_value=True)), \
             mock.patch.object(api, "_semaphore", mock.Mock()), \
             mock.patch.object(api, "planner_llm", planner), \
             mock.patch.object(api, "rewriter_llm", rewriter):
            return client.post("/db_optimize_full", json={
                "database_config": self.CFG,
                "query": self.QUERY,
                "scope": ["sql_optimization"],
                "custom_request": "",
            }), explain_mock

    def test_rewritten_query_plan_is_returned(self):
        after_plan = {"rows": [{"EXPLAIN": "-> Index lookup on opt_customers"}],
                      "columns": ["EXPLAIN"]}
        r, explain_mock = self._post(self.REWRITTEN,
                                     original_explain=after_plan)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["success"])
        self.assertTrue(body["query_changed"])
        self.assertEqual(body["explain_optimized"], after_plan)
        # The re-plan must target the rewritten SQL with the terminator
        # stripped — get_explain_analyze() rejects anything still holding ";".
        explain_mock.assert_called_once()
        planned_sql = explain_mock.call_args.args[1]
        self.assertEqual(planned_sql, self.REWRITTEN.strip().rstrip(";"))
        self.assertNotIn(";", planned_sql)

    def test_unchanged_query_is_not_replanned(self):
        r, explain_mock = self._post(self.QUERY)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertFalse(body["query_changed"])
        self.assertIsNone(body["explain_optimized"])
        explain_mock.assert_not_called()

    def test_replan_failure_does_not_fail_the_optimization(self):
        # The rewriter can emit SQL the engine refuses to plan. Losing the
        # after-plan must not discard a successful optimization.
        r, _ = self._post(self.REWRITTEN,
                          explain_side_effect=RuntimeError("plan boom"))
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["success"])
        self.assertTrue(body["query_changed"])
        self.assertIsNone(body["explain_optimized"])

    def test_unplannable_rewritten_query_yields_null(self):
        # get_explain_analyze() returns None for SQL it cannot plan; the key
        # must still be present so the UI can distinguish "no after-plan"
        # from "field missing on an older backend".
        r, _ = self._post(self.REWRITTEN, original_explain=None)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("explain_optimized", r.json())
        self.assertIsNone(r.json()["explain_optimized"])


class OptimizedQueryExplainSummaryTests(unittest.TestCase):
    """
    The optimized query's plan is summarized in plain English by one extra LLM
    call. It is strictly sequential — it cannot run until the rewriter has
    produced the SQL and that SQL has been re-planned — and strictly best-effort,
    because a summary is a nicety and must never fail an optimization.
    """

    QUERY = "SELECT c1.id FROM opt_customers c1 ORDER BY c1.city;"

    CFG = {"host": "h", "port": 3306, "user": "u", "password": FAKE_PW,
           "database": "d", "ssl_verify": False}

    REWRITTEN = "SELECT c1.id FROM opt_customers c1 ORDER BY c1.id;"

    AFTER_PLAN = {"rows": [{"EXPLAIN": "-> Aggregate  (actual time=0.9 rows=100)"}],
                  "columns": ["EXPLAIN"]}

    GOOD_SUMMARY = {
        "summary": "Aggregates 100 tracks before the cross join.",
        "key_points": ["Materialize eliminated", "Sort over 100 rows"],
    }

    def _post(self, optimized_query, after_plan, summary=None,
              summary_raises=False):
        """
        summary=None    -> summarizer returns nothing usable
        summary_raises -> summarizer blows up mid-call
        """
        rewriter = mock.AsyncMock(return_value={
            "optimized_query": optimized_query, "ddl_statements": [],
            "summary": [], "applied_actions": [1],
            "skipped_actions": [], "warnings": []})
        planner = mock.AsyncMock(return_value={
            "actions": [{"id": 1, "type": "SQL_OPTIMIZATION", "priority": 1,
                         "reason": "sort on an indexed column"}],
            "execution_order": [1]})
        # The pipeline short-circuits before planner/rewriter when no issue is
        # found, which would make query_changed False for unrelated reasons.
        issue_finder = mock.AsyncMock(return_value={"issues": [{
            "id": 1, "scope": "sql_optimization", "title": "Filesort",
            "location": "ORDER BY", "impact": "HIGH",
            "execution_time_cost": "SORT_COST", "root_cause": "sort",
            "evidence": "plan",
        }]})

        if summary_raises:
            summarizer = mock.AsyncMock(side_effect=RuntimeError("llm boom"))
        elif summary is None:
            summarizer = mock.AsyncMock(return_value={})
        else:
            summarizer = mock.AsyncMock(return_value=summary)

        with mock.patch.object(api, "connect_db",
                               return_value=(mock.Mock(), "mysql")), \
             mock.patch.object(api, "_extract_schema",
                               return_value=({}, {"tables": set()})), \
             mock.patch.object(api, "_run_diagnostics_pipeline",
                               return_value={"explain": None, "warnings": {},
                                             "status": {}}), \
             mock.patch.object(api, "_get_explain_analyze",
                               mock.Mock(return_value=after_plan)), \
             mock.patch.object(api, "explain_summarizer_llm", summarizer), \
             mock.patch.object(api, "issue_finder_llm", issue_finder), \
             mock.patch.object(api, "acquire_with_timeout",
                               mock.AsyncMock(return_value=True)), \
             mock.patch.object(api, "_semaphore", mock.Mock()), \
             mock.patch.object(api, "planner_llm", planner), \
             mock.patch.object(api, "rewriter_llm", rewriter):
            return client.post("/db_optimize_full", json={
                "database_config": self.CFG,
                "query": self.QUERY,
                "scope": ["sql_optimization"],
                "custom_request": "",
            }), summarizer

    def test_summary_is_returned_for_the_optimized_plan(self):
        r, summarizer = self._post(self.REWRITTEN, self.AFTER_PLAN,
                                   summary=self.GOOD_SUMMARY)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["success"])
        self.assertTrue(body["query_changed"])
        self.assertEqual(body["explain_optimized_summary"], self.GOOD_SUMMARY)

        # It must be fed the REWRITTEN SQL and the re-planned payload.
        summarizer.assert_awaited_once()
        sent_query, sent_plan = summarizer.call_args.args
        self.assertEqual(sent_query.strip(), self.REWRITTEN.strip())
        self.assertEqual(sent_plan, self.AFTER_PLAN)

    def test_no_summary_when_query_was_not_rewritten(self):
        # Nothing was rewritten, so there is no optimized plan and nothing to
        # summarize — the LLM must not be called at all.
        r, summarizer = self._post(self.QUERY, self.AFTER_PLAN,
                                   summary=self.GOOD_SUMMARY)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertFalse(body["query_changed"])
        self.assertIsNone(body["explain_optimized_summary"])
        summarizer.assert_not_awaited()

    def test_no_summary_when_optimized_plan_is_unavailable(self):
        # The rewriter changed the SQL but the engine could not plan it, so
        # there is no plan to describe.
        r, summarizer = self._post(self.REWRITTEN, None,
                                   summary=self.GOOD_SUMMARY)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["query_changed"])
        self.assertIsNone(body["explain_optimized"])
        self.assertIsNone(body["explain_optimized_summary"])
        summarizer.assert_not_awaited()

    def test_summarizer_failure_does_not_fail_the_optimization(self):
        r, _ = self._post(self.REWRITTEN, self.AFTER_PLAN,
                          summary_raises=True)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["explain_optimized"], self.AFTER_PLAN)
        self.assertIsNone(body["explain_optimized_summary"])

    def test_empty_summary_is_reported_as_null(self):
        # An unusable model response must normalize to null, not {} or "".
        r, _ = self._post(self.REWRITTEN, self.AFTER_PLAN, summary={})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("explain_optimized_summary", r.json())
        self.assertIsNone(r.json()["explain_optimized_summary"])

class ExplainSummarizerPromptTests(unittest.TestCase):
    """
    Unit tests for the plan flattening the summarizer prompt depends on. Both
    engine shapes must produce usable prompt text, and junk must produce none
    so the stage skips the LLM call entirely.
    """

    def test_mysql_plan_rows_are_joined(self):
        from llm.explain_summarizer import format_explain_plan_for_prompt
        payload = {"rows": [
            {"EXPLAIN": "-> Sort  (actual time=1..2 rows=100)"},
            {"EXPLAIN": "    -> Aggregate using temporary table"},
        ], "columns": ["EXPLAIN"]}
        text = format_explain_plan_for_prompt(payload)
        self.assertIn("-> Sort", text)
        self.assertIn("Aggregate using temporary table", text)

    def test_postgres_prefers_the_flattened_summary(self):
        from llm.explain_summarizer import format_explain_plan_for_prompt
        payload = {
            "engine": "postgres",
            "summary": {"Execution Time": 12.3,
                        "child_node_types": ["Hash Join", "Seq Scan"]},
            "rows": [{"QUERY PLAN": '{"Plan": {"nested json"}}'}],
            "columns": ["QUERY PLAN"],
        }
        text = format_explain_plan_for_prompt(payload)
        self.assertIn("Execution Time: 12.3", text)
        self.assertIn("Hash Join, Seq Scan", text)
        # The raw JSON tree is not what the model should read.
        self.assertNotIn("nested json", text)

    def test_unusable_payloads_produce_no_text(self):
        from llm.explain_summarizer import format_explain_plan_for_prompt
        for payload in (None, {}, {"rows": []}, {"rows": [{"EXPLAIN": "  "}]},
                        {"rows": "not-a-list"}):
            self.assertEqual(format_explain_plan_for_prompt(payload), "",
                             msg=f"payload={payload!r}")

    def test_no_call_when_the_plan_is_empty(self):
        import asyncio as _asyncio
        from llm.explain_summarizer import explain_summarizer_llm
        result = _asyncio.run(explain_summarizer_llm("SELECT 1", None))
        self.assertEqual(result, {})

    def test_malformed_model_output_normalizes_to_empty(self):
        # Whitespace-only summary text is not a summary.
        import asyncio as _asyncio
        from llm import explain_summarizer as mod
        with mock.patch.object(mod, "safe_json_call",
                               mock.AsyncMock(return_value={
                                   "summary": "   ", "key_points": ["a"]})):
            result = _asyncio.run(
                mod.explain_summarizer_llm(
                    "SELECT 1",
                    {"rows": [{"EXPLAIN": "-> Sort"}], "columns": ["EXPLAIN"]},
                )
            )
        self.assertEqual(result, {})

    def test_key_points_are_filtered_to_strings(self):
        import asyncio as _asyncio
        from llm import explain_summarizer as mod
        with mock.patch.object(mod, "safe_json_call",
                               mock.AsyncMock(return_value={
                                   "summary": "Reads 100 rows.",
                                   "key_points": ["ok", "", None, 7, "also ok"]})):
            result = _asyncio.run(
                mod.explain_summarizer_llm(
                    "SELECT 1",
                    {"rows": [{"EXPLAIN": "-> Sort"}], "columns": ["EXPLAIN"]},
                )
            )
        self.assertEqual(result, {"summary": "Reads 100 rows.",
                                  "key_points": ["ok", "also ok"]})
if __name__ == "__main__":
    unittest.main(verbosity=2)

