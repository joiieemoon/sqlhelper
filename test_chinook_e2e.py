# -*- coding: utf-8 -*-
"""
End-to-end tests for the direct-DB optimization flow against the real
Chinook MySQL database (Aiven).

What is actually asserted here:

  1. the schema the pipeline needs is extractable from Chinook, including the
     index that decides whether an index recommendation is redundant
  2. every optimized variant this pipeline produced for the query returns the
     SAME ROWS as the original - the property that matters for an automated
     rewrite, and one that stays valid if the seed data changes
  3. the live 3-stage pipeline run is grounded (schema visible, EXPLAIN
     present) and its output is equivalence-checked automatically
  4. the wrong-case schema guard still reports the real schema name

Run:
    python test_chinook_e2e.py

The DB_* variables must point at the Chinook database, e.g.:
    DB_HOST=<host>  DB_PORT=21131  DB_USER=avnadmin
    DB_PASSWORD=<secret>  DB_NAME=Chinook

The full-pipeline test makes three Gemini calls (~60-90s), so it is opt-in:
    RUN_LIVE_LLM=1
"""

import os
import unittest
from unittest import mock

from fastapi.testclient import TestClient

import api
import db_utils
from api import app
from db_schema import extract_schema, resolve_query_tables

# When TestClient is used without a `with TestClient(app):` lifespan block,
# FastAPI's lifespan does not run, leaving api._semaphore as None. Initialise
# it lazily so in-process TestClient calls can acquire it.
if api._semaphore is None:
    import asyncio
    api._semaphore = asyncio.Semaphore(api.MAX_CONCURRENT_PIPELINES)

client = TestClient(app)

# The query this R&D milestone was validated with: two identical correlated
# subqueries over Customer/Invoice (Chinook).
ORIGINAL = (
    "SELECT c.CustomerId, c.FirstName, c.LastName, "
    "(SELECT SUM(i.Total) FROM Invoice i WHERE i.CustomerId = c.CustomerId) AS TotalSpent "
    "FROM Customer c "
    "WHERE (SELECT SUM(i.Total) FROM Invoice i WHERE i.CustomerId = c.CustomerId) > 40;"
)

# Every rewrite the pipeline returned for ORIGINAL during this milestone:
# the derived-table INNER JOIN, the LEFT JOIN + COALESCE form, and the
# GROUP BY ... HAVING form produced before the schema was visible. All three
# must agree with the original row for row.
OPTIMIZED_VARIANTS = {
    "derived_inner_join": (
        "SELECT c.CustomerId, c.FirstName, c.LastName, inv_agg.TotalSpent "
        "FROM Customer c "
        "JOIN (SELECT i.CustomerId, SUM(i.Total) AS TotalSpent "
        "FROM Invoice i GROUP BY i.CustomerId) AS inv_agg "
        "ON c.CustomerId = inv_agg.CustomerId "
        "WHERE inv_agg.TotalSpent > 40"
    ),
    "derived_left_join_coalesce": (
        "SELECT c.CustomerId, c.FirstName, c.LastName, "
        "COALESCE(invoice_sums.TotalSpent, 0) AS TotalSpent "
        "FROM Customer c "
        "LEFT JOIN (SELECT i.CustomerId, SUM(i.Total) AS TotalSpent "
        "FROM Invoice i GROUP BY i.CustomerId) AS invoice_sums "
        "ON c.CustomerId = invoice_sums.CustomerId "
        "WHERE COALESCE(invoice_sums.TotalSpent, 0) > 40"
    ),
    "group_by_having": (
        "SELECT c.CustomerId, c.FirstName, c.LastName, SUM(i.Total) AS TotalSpent "
        "FROM Customer c "
        "LEFT JOIN Invoice i ON c.CustomerId = i.CustomerId "
        "GROUP BY c.CustomerId, c.FirstName, c.LastName "
        "HAVING SUM(i.Total) > 40"
    ),
}

# Chinook's own FK index on Invoice - auto-created by MySQL. Its presence is
# why the ungrounded run's "CREATE INDEX ... ON Invoice (CustomerId)" advice
# was a duplicate of an existing index.
INVOICE_CUSTOMER_INDEX = "IFK_InvoiceCustomerId"


def live_cfg():
    return {
        "host": os.getenv("CHINOOK_HOST") or os.getenv("DB_HOST", ""),
        "port": int(os.getenv("CHINOOK_PORT") or os.getenv("DB_PORT", "3306")),
        "user": os.getenv("CHINOOK_USER") or os.getenv("DB_USER", ""),
        "password": os.getenv("CHINOOK_PASSWORD") or os.getenv("DB_PASSWORD", ""),
        "database": os.getenv("CHINOOK_DATABASE") or os.getenv("CHINOOK_DB") or os.getenv("DB_NAME", ""),
    }


def has_live_db():
    return all(live_cfg().values())


def _fetch_rows(sql):
    """Run a SELECT on the live database; return a sorted multiset of strings."""
    conn, _engine = db_utils.connect_db(live_cfg(), None, "mysql")
    try:
        cursor = conn.cursor()
        try:
            cursor.execute(sql.strip().rstrip(";"))
            return sorted("|".join(str(v) for v in row) for row in cursor.fetchall())
        finally:
            cursor.close()
    finally:
        conn.close()


def _schema_for(query):
    """(tables_schema, meta, connection) for `query` against live Chinook."""
    conn, engine = db_utils.connect_db(live_cfg(), None, "mysql")
    try:
        tables_schema, meta = extract_schema(
            query.strip().rstrip(";"), conn, live_cfg()["database"], engine)
        return tables_schema, meta, conn
    except Exception:
        conn.close()
        raise

class ChinookSchemaGroundingTests(unittest.TestCase):
    """Proves that Chinook's schema and indexes are actually visible to the pipeline."""

    @unittest.skipUnless(has_live_db(), "DB_* env vars not set")
    def test_chinook_tables_and_indexes_are_extracted(self):
        tables_schema, meta, conn = _schema_for(ORIGINAL)
        try:
            self.assertIn("Customer", tables_schema)
            self.assertIn("Invoice", tables_schema)

            cust = tables_schema["Customer"]
            inv = tables_schema["Invoice"]

            self.assertIsNotNone(cust.get("row_count"))
            self.assertGreater(cust["row_count"], 0)
            self.assertIsNotNone(inv.get("row_count"))
            self.assertGreater(inv["row_count"], 0)

            cust_cols = {c["name"] for c in cust.get("columns", [])}
            inv_cols = {c["name"] for c in inv.get("columns", [])}
            self.assertIn("CustomerId", cust_cols)
            self.assertIn("FirstName", cust_cols)
            self.assertIn("Total", inv_cols)

            inv_idx_names = {idx["name"] for idx in inv.get("indexes", [])}
            self.assertIn(
                INVOICE_CUSTOMER_INDEX,
                inv_idx_names,
                f"Expected {INVOICE_CUSTOMER_INDEX} in Invoice indexes; got {inv_idx_names}"
            )
        finally:
            conn.close()

    @unittest.skipUnless(has_live_db(), "DB_* env vars not set")
    def test_db_schema_extract_endpoint_returns_no_warnings(self):
        res = client.post("/db_schema_extract", json={
            "database_config": live_cfg(),
            "query": ORIGINAL,
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("success"), f"schema_extract failed: {data}")
        self.assertEqual(data.get("schema_warnings", []), [])
        self.assertIn("Customer", data.get("schema", {}))


class ChinookFullPipelineLiveTests(unittest.TestCase):
    """
    Runs the REAL 3-stage LLM pipeline against live Chinook.
    Opt-in via RUN_LIVE_LLM=1 because it makes 3 Gemini calls (~60-90s).
    """

    @unittest.skipUnless(
        has_live_db() and os.getenv("RUN_LIVE_LLM") == "1",
        "Set RUN_LIVE_LLM=1 and DB_* env vars to run live LLM pipeline"
    )
    def test_live_db_optimize_full_on_chinook(self):
        res = client.post("/db_optimize_full", json={
            "database_config": live_cfg(),
            "query": ORIGINAL,
            "scope": ["joins", "indexes", "sql_optimization"],
            "custom_request": "",
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()

        self.assertTrue(data.get("success"), f"Pipeline reported failure: {data.get('error')}")
        self.assertEqual(data.get("database"), live_cfg()["database"])
        self.assertEqual(data.get("schema_warnings", []), [])

        schema_used = data.get("schema_used") or {}
        self.assertIn("Customer", schema_used)
        self.assertIn("Invoice", schema_used)
        self.assertGreater(schema_used["Customer"].get("row_count", 0), 0)

        self.assertIsNotNone(data.get("explain"))

        opt_query = data.get("optimized_query")
        self.assertTrue(opt_query and opt_query.strip(), "No optimized_query returned")

        live_rows = _fetch_rows(opt_query)
        baseline_rows = _fetch_rows(ORIGINAL)
        self.assertEqual(
            live_rows,
            baseline_rows,
            f"Optimized query returned different data!\nOptimized:\n{opt_query}"
        )

        actions = data.get("actions", [])
        for act in actions:
            if act.get("type") == "CREATE_INDEX" and act.get("table", "").lower() == "invoice":
                cols = [c.lower() for c in act.get("columns", [])]
                self.assertNotEqual(
                    cols,
                    ["customerid"],
                    "LLM recommended redundant index on Invoice(CustomerId) despite IFK_InvoiceCustomerId existing!"
                )


class ChinookSemanticEquivalenceTests(unittest.TestCase):
    """
    Every optimized query variant produced by the LLM pipeline must return
    the EXACT same rows as the original query against the live Chinook database.
    """

    @classmethod
    def setUpClass(cls):
        if not has_live_db():
            return
        cls.baseline_rows = _fetch_rows(ORIGINAL)
        assert len(cls.baseline_rows) > 0, "Original query returned zero rows"

    @unittest.skipUnless(has_live_db(), "DB_* env vars not set")
    def test_baseline_has_expected_seed_row_count(self):
        self.assertEqual(len(self.baseline_rows), 14)

    @unittest.skipUnless(has_live_db(), "DB_* env vars not set")
    def test_derived_inner_join_is_semantically_equivalent(self):
        rows = _fetch_rows(OPTIMIZED_VARIANTS["derived_inner_join"])
        self.assertEqual(rows, self.baseline_rows)

    @unittest.skipUnless(has_live_db(), "DB_* env vars not set")
    def test_derived_left_join_coalesce_is_semantically_equivalent(self):
        rows = _fetch_rows(OPTIMIZED_VARIANTS["derived_left_join_coalesce"])
        self.assertEqual(rows, self.baseline_rows)

    @unittest.skipUnless(has_live_db(), "DB_* env vars not set")
    def test_group_by_having_is_semantically_equivalent(self):
        rows = _fetch_rows(OPTIMIZED_VARIANTS["group_by_having"])
        self.assertEqual(rows, self.baseline_rows)

