# -*- coding: utf-8 -*-
"""
test_db_endpoint_live.py — live tests for POST /db_optimize_test

Prerequisites:
  - Server running on http://127.0.0.1:8000
  - backend/.env has DB_PASSWORD set (gitignored)
Tests:
  1. valid connection + SELECT 1 + user SELECT
  2. write query rejected
  3. missing config
  4. invalid credentials
  5. connection closed / no secret leak
"""

from __future__ import annotations
import json
import urllib.request
import urllib.error
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

BASE = "http://127.0.0.1:8000/db_optimize_test"

DB_HOST = os.getenv("DB_HOST", "mysql-sql-optimizer-test01-sql-optimizer-test01.l.aivencloud.com")
DB_PORT = int(os.getenv("DB_PORT", "17027"))
DB_USER = os.getenv("DB_USER", "avnadmin")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME", "sql_optimizer_test")

if not DB_PASSWORD:
    raise SystemExit("ERROR: DB_PASSWORD not set in backend/.env")


def post(payload):
    req = urllib.request.Request(
        BASE, data=json.dumps(payload).encode(), method="POST"
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Device-ID", "db-live-test")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())
    except Exception as e:
        return -1, {"error": str(e)}


DB_CFG = {
    "host": DB_HOST,
    "port": DB_PORT,
    "user": DB_USER,
    "password": DB_PASSWORD,
    "database": DB_NAME,
}

FAKE_SECRET = "FAKE_SECRET_PW_TEST_12345"


def show(label, status, body):
    print(f"\n{label}")
    print(f"  HTTP {status}")
    print(f"  success  = {body.get('success')}")
    print(f"  error    = {body.get('error')}")
    print(f"  database = {body.get('database')}")
    print(f"  rows     = {body.get('rows')}")
    print(f"  secret_in_response = {FAKE_SECRET in json.dumps(body)}")


def main():
    print("=" * 60)
    print("Live /db_optimize_test tests")
    print("=" * 60)

    # 1) valid connection + SELECT 1 + user SELECT
    print("\n--- Test 1: valid connection + SELECT 1 + user SELECT ---")
    # test SELECT 1
    ok, err = post({"database_config": DB_CFG})  # no query -> expects SELECT 1
    show("1a. bare connection (SELECT 1)", ok, err)
    assert ok == 200 and err.get("success") is True and err.get("rows") == [["1"]], (
        f"d1 failed: {ok} {err}"
    )

    # test user SELECT
    ok, err = post({"database_config": DB_CFG, "query": "SELECT DATABASE()"})
    show("1b. SELECT DATABASE()", ok, err)
    assert ok == 200 and err.get("success") is True
    assert err.get("rows")[0][0] == DB_NAME, f"db mismatch: {err}"

    # test against real data
    ok, err = post({
        "database_config": DB_CFG,
        "query": "SELECT id, name, country, status FROM customers WHERE country = 'USA' AND status = 'active'",
    })
    show("1c. customers USA+active", ok, err)
    assert ok == 200 and err.get("success") is True

    # 2) write query rejected
    print("\n--- Test 2: write query rejected ---")
    ok, err = post({
        "database_config": DB_CFG,
        "query": "DELETE FROM customers",
    })
    show("2. DELETE rejected", ok, err)
    assert not err.get("success")
    assert "SELECT" in (err.get("error") or "")
    assert FAKE_SECRET not in json.dumps(err)

    # 3) missing config
    print("\n--- Test 3: missing config ---")
    ok, err = post({"query": "SELECT 1"})
    show("3. missing database_config", ok, err)
    assert FAKE_SECRET not in json.dumps(err)

    # 4) invalid credentials
    print("\n--- Test 4: invalid credentials ---")
    wrong_cfg = dict(DB_CFG)
    wrong_cfg["password"] = "wrong_password_xyz"
    ok, err = post({"database_config": wrong_cfg, "query": "SELECT 1"})
    show("4. wrong password", ok, err)
    assert ok == 400
    assert "password" in (err.get("error") or "").lower()
    assert FAKE_SECRET not in json.dumps(err)
    assert "wrong_password_xyz" not in json.dumps(err)

    # 5) connection closed + no leak
    print("\n--- Test 5: connection closed / no leak ---")
    ok, err = post({"database_config": DB_CFG, "query": "SELECT 1"})
    show("5. SELECT 1 (connection lifecycle)", ok, err)
    assert ok == 200 and err.get("success") is True
    assert FAKE_SECRET not in json.dumps(err)

    print("\n" + "=" * 60)
    print("ALL LIVE TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()