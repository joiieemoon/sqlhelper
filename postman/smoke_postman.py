# -*- coding: utf-8 -*-
"""Smoke-tests the exact Postman collection payloads against the running server."""
import json, urllib.request, urllib.error, io, sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
BASE = "http://127.0.0.1:8000"
DEV = "postman-smoke-1"

def call(method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    r.add_header("X-Device-ID", DEV)
    try:
        with urllib.request.urlopen(r, timeout=330) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}
    except Exception as e:
        return -1, {"error": str(e)}

def show(label, status, body):
    err = body.get("error")
    oq = (body.get("optimized_query") or "")[:70].replace("\n", " ")
    rem = body.get("queries_remaining")
    print(f"[{label}] HTTP {status} | error={err!r} | optimized={oq!r} | remaining={rem}")

s, b = call("GET", "/health")
print(f"[health] HTTP {s} | {json.dumps(b)[:140]}")

s, b = call("POST", "/agent_optimize", {
    "query": "SELECT id, name FROM customers WHERE country = 'USA' AND status = 'active' ORDER BY name",
    "agent_version": 1, "custom_request": "", "scope": []})
show("agent optimize (happy)", s, b)

s, b = call("POST", "/agent_optimize", {
    "query": "DELETE FROM customers WHERE id = 1", "agent_version": 1})
show("negative: write query", s, b)

s, b = call("POST", "/agent_optimize", {"query": "SELECT id FROM customers LIMIT 1"})
show("negative: no agent_version", s, b)

s, b = call("POST", "/optimize", {
    "query": "SELECT id, name FROM customers WHERE country = 'USA' AND status = 'active' ORDER BY name",
    "custom_request": "Keep it MySQL 8 compatible."})
show("syntax-only optimize", s, b)
