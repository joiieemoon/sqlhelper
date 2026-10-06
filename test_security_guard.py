"""
test_security_guard.py — multi-layer SELECT-only guard, defence in depth.

Run:  python test_security_guard.py

Covers the bypass classes that matter: top-level and hidden DML/DDL, stacked
statements, MySQL executable comments, NUL-byte truncation, INTO OUTFILE,
variable assignment, dangerous functions, dialect confusion, and that every
HTTP route actually enforces the guard.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# The API tests below share one TestClient identifier; the product limit of
# 5 optimizations/day would otherwise 429 them mid-run. Must be set before the
# validator/api import chain reads it at import time.
os.environ.setdefault("RATE_LIMIT_MAX", "100000")

from validator.query_validator import MAX_QUERY_LENGTH, validate_query  # noqa: E402


# ── Every one of these MUST be rejected ──────────────────────────────
ATTACK_QUERIES = [
    # top-level DML
    "DELETE FROM users",
    "UPDATE users SET admin=1",
    "INSERT INTO users VALUES (1)",
    "REPLACE INTO users VALUES (1)",
    "TRUNCATE TABLE users",
    # DDL
    "DROP TABLE users",
    "ALTER TABLE users ADD COLUMN x INT",
    "CREATE TABLE t (a INT)",
    "CREATE DATABASE evil",
    "RENAME TABLE a TO b",
    # session / transaction control
    "SET GLOBAL max_connections=9999",
    "SET @x = 1",
    "USE mysql",
    "COMMIT",
    "ROLLBACK",
    # stacked / multi statement
    "SELECT 1; DROP TABLE users",
    "SELECT 1; DELETE FROM users",
    "SELECT 1;SELECT 2;SELECT 3",
    # DML hidden in a CTE
    "WITH x AS (DELETE FROM users RETURNING *) SELECT * FROM x",
    "WITH x AS (UPDATE users SET a=1 RETURNING *) SELECT * FROM x",
    # DML hidden in a subquery
    "SELECT * FROM (DELETE FROM users) AS t",
    # INTO outfile / dumpfile / user variable
    "SELECT * FROM users INTO OUTFILE '/tmp/x'",
    "SELECT * FROM users INTO DUMPFILE '/tmp/x'",
    "SELECT 1 INTO @v",
    # variable assignment
    "SELECT @x := 1",
    "SELECT a, @v := a FROM t",
    # dangerous functions
    "SELECT LOAD_FILE('/etc/passwd')",
    "SELECT SLEEP(10)",
    "SELECT BENCHMARK(1000000, MD5('a'))",
    # MySQL executable comment: sqlglot sees a comment, MySQL executes it
    "SELECT 1 /*!50000,(SELECT 1 FROM (SELECT 1)x) */",
    "SELECT /*!32302 1 */",
    "SELECT 1 /*+ MAX_EXECUTION_TIME(1) */",
    "SELECT 1 /*!99999 UNION SELECT 2 */",
    # NUL byte truncation / control characters
    "SELECT 1\x00; DROP TABLE users",
    "SELECT 1\x07 FROM users",
    # CALL / handler / prepared statements
    "CALL some_proc()",
    "HANDLER t OPEN",
    "PREPARE s FROM 'SELECT 1'",
    # grants
    "GRANT ALL ON *.* TO 'x'@'%'",
    "LOAD DATA INFILE 'x' INTO TABLE t",
    # empty
    "",
    "   ",
]

# ── These MUST be accepted (no false positives) ──────────────────────
SAFE_QUERIES = [    
    "SELECT 1",
    "SELECT id, name FROM users",
    "SELECT * FROM customers WHERE country = 'USA' AND status = 'active'",
    "SELECT COUNT(*) FROM users",
    "SELECT * FROM a JOIN b ON a.id = b.id WHERE a.x = 1 ORDER BY a.id LIMIT 10",
    "SELECT * FROM t WHERE email LIKE '%@gmail.com'",
    "SELECT * FROM t UNION SELECT * FROM u",
    "SELECT * FROM t EXCEPT SELECT * FROM u",
    "SELECT * FROM t INTERSECT SELECT * FROM u",
    "WITH cte AS (SELECT id FROM t) SELECT * FROM cte",
    "SELECT DISTINCT country FROM customers",
    "SELECT COALESCE(a, b) FROM t",
    "SELECT DATE_FORMAT(created_at, '%Y-%m') FROM orders",
    # identifiers that merely CONTAIN a keyword must not trip the guard
    "SELECT dropped_at, updated_at, created_at FROM audit_log",
    "SELECT * FROM t WHERE name = 'delete me'",
    "SELECT * FROM t WHERE status = 'insert pending'",
    "SELECT CAST(x AS UNSIGNED) FROM t",
    "SELECT * FROM users WHERE email LIKE '%@gmail.com';",
    "SELECT\n  id\nFROM\n  users",
    # read-only shapes the backend also accepts (parity with the frontend)
    "VALUES ROW(1)",
    "(SELECT 1)",
]


class TestReadOnlyShapesAccepted(unittest.TestCase):
    """Shapes that look unusual but are genuinely read-only."""

    def test_values_row(self):
        self.assertEqual(validate_query("VALUES ROW(1)"), "FINE")

    def test_parenthesised_select(self):
        self.assertEqual(validate_query("(SELECT 1)"), "FINE")


class TestLayer0Shape(unittest.TestCase):
    """L0 — size, emptiness, NUL bytes, control characters."""

    def test_empty(self):
        for q in ("", "   ", None):
            self.assertIn("Empty query", validate_query(q))

    def test_oversize(self):
        self.assertIn("max length", validate_query("SELECT 1 " + "a" * MAX_QUERY_LENGTH))

    def test_nul_byte(self):
        self.assertIn("null byte", validate_query("SELECT 1\x00"))

    def test_control_char(self):
        self.assertIn("control character", validate_query("SELECT\x07 1"))


class TestWriteStatementsBlocked(unittest.TestCase):
    """L1/L3/L4 — no DML or DDL may pass, however it is hidden."""

    def test_all_attacks_rejected(self):
        for query in ATTACK_QUERIES:
            with self.subTest(query=query[:60]):
                result = validate_query(query)
                self.assertNotEqual(
                    result, "FINE",
                    f"SECURITY BYPASS: {query!r} was accepted",
                )
                self.assertTrue(result.startswith("Error:"), result)

    def test_both_dialects_reject_writes(self):
        for dialect in ("mysql", "postgres"):
            for q in ("DELETE FROM t", "DROP TABLE t", "UPDATE t SET a=1",
                      "INSERT INTO t VALUES (1)", "TRUNCATE TABLE t"):
                with self.subTest(dialect=dialect, q=q):
                    self.assertNotEqual(validate_query(q, dialect=dialect), "FINE")


class TestSafeQueriesAccepted(unittest.TestCase):
    """No false positives — real optimization queries must still work."""

    def test_safe_queries_pass(self):
        for query in SAFE_QUERIES:
            with self.subTest(query=query[:60]):
                self.assertEqual(validate_query(query), "FINE", validate_query(query))

    def test_union_allowed(self):
        """A compound read-only SELECT is legitimate; the old guard rejected it."""
        self.assertEqual(
            validate_query("SELECT a FROM t UNION SELECT a FROM u"), "FINE"
        )

    def test_cte_allowed(self):
        self.assertEqual(
            validate_query("WITH c AS (SELECT 1 AS x) SELECT x FROM c"), "FINE"
        )


class TestDialectHandling(unittest.TestCase):
    def test_pg_cast_accepted_with_pg_dialect(self):
        # The security property is that WRITES are rejected in every dialect;
        # whether `::` parses under the MySQL reader is a sqlglot tolerance,
        # not a safety concern.
        self.assertEqual(validate_query("SELECT id::text FROM customers",
                                        dialect="postgres"), "FINE")

    def test_writes_rejected_in_every_dialect(self):
        for dialect in ("mysql", "postgres", "mariadb", "pg", "weird-db"):
            for q in ("DELETE FROM t", "DROP TABLE t", "TRUNCATE t"):
                with self.subTest(dialect=dialect, q=q):
                    self.assertNotEqual(validate_query(q, dialect=dialect), "FINE")

    def test_unknown_dialect_falls_back_to_mysql(self):
        self.assertEqual(validate_query("SELECT 1", dialect="weird-db"), "FINE")
        self.assertNotEqual(validate_query("DELETE FROM t", dialect="weird-db"), "FINE")


class TestEveryRouteIsGuarded(unittest.TestCase):
    """
    End-to-end: the guard must be enforced at the HTTP layer, not merely
    present in the validator. A route that forgets to call it is a bypass.
    """

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        import api as api_mod
        cls.api = api_mod
        cls.client = TestClient(api_mod.app)

    def _headers(self):
        return {"X-Device-ID": f"sec-test-{os.getpid()}"}

    def test_optimize_rejects_delete(self):
        """Regression: /optimize had NO validation at all."""
        r = self.client.post(
            "/optimize",
            json={"query": "DELETE FROM users"},
            headers=self._headers(),
        )
        body = r.json()
        self.assertTrue(body.get("error"), "expected an error for DELETE")
        self.assertNotIn("optimized_query", body)

    def test_optimize_rejects_update(self):
        r = self.client.post(
            "/optimize",
            json={"query": "UPDATE users SET admin=1"},
            headers=self._headers(),
        )
        self.assertNotIn("optimized_query", r.json())

    def test_agent_optimize_rejects_delete(self):
        r = self.client.post(
            "/agent_optimize",
            json={"query": "DELETE FROM users", "agent_version": 99},
            headers=self._headers(),
        )
        self.assertNotIn("optimized_query", r.json())

    def test_db_routes_reject_delete_without_connecting(self):
        """Rejected before any DB connection is attempted."""
        from unittest import mock

        cfg = {"host": "h", "port": 3306, "user": "u",
               "password": "p", "database": "d"}
        for endpoint in ("/db_optimize", "/db_schema_extract", "/db_explain_test",
                         "/db_optimize_full", "/db_optimize_test"):
            with self.subTest(endpoint=endpoint):
                with mock.patch.object(
                    self.api, "connect_db",
                    side_effect=AssertionError("must not connect"),
                ):
                    r = self.client.post(
                        endpoint,
                        json={"database_config": cfg, "query": "DELETE FROM users"},
                        headers=self._headers(),
                    )
                    self.assertFalse(r.json().get("success", False))
                    self.assertIn("error", r.json())


class TestPasswordNeverLeaked(unittest.TestCase):
    """Credentials must not appear in responses."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        import api as api_mod
        cls.client = TestClient(api_mod.app)

    def test_password_absent_from_error_response(self):
        secret = "SuperSecret123!"
        r = self.client.post(
            "/db_optimize_test",
            json={"database_config": {"host": "no-such-host.invalid",
                                      "port": 3306, "user": "u",
                                      "password": secret, "database": "d"}},
            headers={"X-Device-ID": f"sec-pw-{os.getpid()}"},
        )
        self.assertNotIn(secret, r.text)
        self.assertNotIn("Traceback", r.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
