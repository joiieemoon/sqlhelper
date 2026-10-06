# -*- coding: utf-8 -*-
"""Generates the complete Postman collection + environment for the SQL Optimizer API."""
import json, os

BASE = os.path.dirname(os.path.abspath(__file__))
COLL = os.path.join(BASE, "SQL-Optimizer.postman_collection.json")
ENVF = os.path.join(BASE, "SQL-Optimizer-Local.postman_environment.json")

def body(raw):
    return {"mode": "raw", "raw": raw, "options": {"raw": {"language": "json"}}}

def req(name, method, path, b=None, desc=""):
    r = {"method": method, "header": [
            {"key": "X-Device-ID", "value": "{{device_id}}",
             "description": "Per-device rate-limit identifier (required for accurate quota buckets)"}
         ],
         "url": "{{base_url}}" + path, "description": desc}
    if b is not None:
        r["body"] = b
        r["header"].append({"key": "Content-Type", "value": "application/json"})
    return {"name": name, "request": r}

opt_payload = json.dumps({
    "query": "SELECT id, name FROM customers WHERE country = 'USA' AND status = 'active' ORDER BY name",
    "agent_version": 1,
    "custom_request": "",
    "scope": []
}, indent=2)

custom_payload = json.dumps({
    "query": "SELECT id, name FROM customers WHERE country = 'USA' AND status = 'active' ORDER BY name",
    "agent_version": 1,
    "custom_request": "Do not recommend any new indexes; focus on rewriting the query only.",
    "scope": ["syntax", "rewriting"]
}, indent=2)

syntax_payload = json.dumps({
    "query": "SELECT id, name FROM customers WHERE country = 'USA' AND status = 'active' ORDER BY name",
    "custom_request": "Keep it MySQL 8 compatible."
}, indent=2)

bad_payload = json.dumps({
    "query": "DELETE FROM customers WHERE id = 1",
    "agent_version": 1
}, indent=2)

missing_version_payload = json.dumps({
    "query": "SELECT id FROM customers LIMIT 1"
}, indent=2)

throttle_payload = json.dumps({
    "query": "SELECT id, name, email, created_at FROM customers c LEFT JOIN orders o ON c.id = o.customer_id WHERE c.country = 'USA' AND c.status = 'active' AND o.total > 100 ORDER BY o.created_at DESC LIMIT 100",
    "agent_version": 1
}, indent=2)

db_payload = json.dumps({
    "database_config": {
        "host": "{{db_host}}",
        "port": 17027,
        "user": "{{db_user}}",
        "password": "{{db_password}}",
        "database": "{{db_name}}"
    },
    "query": "SELECT 1"
}, indent=2)
\n
explain_payload = json.dumps(
    {
        "database_config": {
            "host": "{{db_host}}",
            "port": 17027,
            "user": "{{db_user}}",
            "password": "{{db_password}}",
            "database": "{{db_name}}"
        },
        "query": "SELECT id, name FROM customers WHERE country = 'USA' AND status = 'active'"
    },
    indent=2
)
items = [
    {"name": "Health", "item": [
        req("Health Check", "GET", "/health", desc="Liveness + active LLM config (gemini-vertex / model / location / project) and concurrency limits."),
    ]},
    {"name": "Agent Optimize (full pipeline)", "item": [
        req("Optimize Query (agent)", "POST", "/agent_optimize", body(opt_payload),
            "MAIN ENDPOINT. Runs the Gemini multi-agent pipeline (issue finder -> planner -> rewriter).\n\n"
            "Required body fields:\n"
            "  - query: single SELECT statement (required)\n"
            "  - agent_version: int >= 1 (required, else 426 OUTDATED_AGENT)\n\n"
            "Optional: custom_request, scope, schema, explain_analyze, innodb_stats.\n"
            "Send X-Device-ID header for per-device rate limiting.\n\n"
            "Success 200: {optimized_query, actions, index_recommendations, summary, error: null, queries_used, queries_remaining}\n"
            "Errors: 426 outdated agent | 429 rate limited | 503 LLM rate-limited (retryable) | 504 timeout"),
        req("Optimize Query (throttle-like join)", "POST", "/agent_optimize", body(throttle_payload),
            "Heavier join/order/limit query — same shape as backend/throttle_payload.json."),
        req("Optimize (custom request)", "POST", "/agent_optimize", body(custom_payload),
            "Passes custom_request so the rewriter honors extra instructions (e.g. avoid index changes)."),
        req("NEGATIVE: write query rejected", "POST", "/agent_optimize", body(bad_payload),
            "Validator only allows a single SELECT — expect HTTP 200 with error: 'Error: Only SELECT queries are allowed.'"),
        req("NEGATIVE: missing agent_version", "POST", "/agent_optimize", body(missing_version_payload),
            "Expect HTTP 426 with {error: 'OUTDATED_AGENT', message: '...update to continue.'}"),
    ]},
    {"name": "Direct DB Connection (R&D)", "item": [
        req("DB Connection Test", "POST", "/db_optimize_test", body(db_payload),
            "NEW R&D endpoint: proves FastAPI -> supplied MySQL (SSL required) -> query execution.\n"
            "Set {{db_password}} in the environment first (never hardcode it).\n"
            "query is optional — omit for a bare SELECT 1 connection test.\n"
            "SELECT-only; password is never logged or returned."),
        req("DB Schema Extract", "POST", "/db_schema_extract", body(schema_payload),
            "R&D Step 1: connects to the supplied MySQL, extracts tables/columns/indexes\n"
            "for the query's tables. Output shape matches what issue_finder_llm expects.\n"
            "Set {{db_host}}/{{db_user}}/{{db_password}}/{{db_name}} in the environment."),
    ]},
    {"name": "Syntax-only Optimize", "item": [
        req("Optimize (syntax only)", "POST", "/optimize", body(syntax_payload),
            "Lighter 2-stage pipeline (syntax planner -> syntax rewriter), no schema/index analysis.\n"
            "Body: {query, custom_request (optional)}. Same response shape as /agent_optimize."),
    ]},
]

collection = {
    "info": {
        "_postman_id": "b7e1c0de-1111-4a2b-9c3d-sqlopt000001",
        "name": "SQL Optimizer API",
        "description": "Postman setup for the SQL Optimizer backend (FastAPI + Gemini via Vertex AI).\n\n"
                       "1. Start the server: run backend\\run.bat (or run_server_now.ps1).\n"
                       "2. Import SQL-Optimizer.postman_collection.json and SQL-Optimizer-Local.postman_environment.json.\n"
                       "3. Select the 'SQL Optimizer - Local' environment and hit the requests.",
        "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
    },
    "item": items,
    "event": [
        {
            "listen": "prerequest",
            "script": {"type": "text/javascript", "exec": [
                "// Unique device id per Postman install for rate-limit buckets",
                "if (!pm.environment.get('device_id')) {",
                "    pm.environment.set('device_id', 'postman-' + Math.random().toString(36).slice(2, 10));",
                "}"
            ]}
        },
        {
            "listen": "test",
            "script": {"type": "text/javascript", "exec": [
                "// Lenient collection-level script: captures fields, never fails negative tests",
                "if (pm.response.headers.get('Content-Type') && pm.response.headers.get('Content-Type').includes('json')) {",
                "    const j = pm.response.json();",
                "    if (j.optimized_query !== undefined) pm.collectionVariables.set('last_optimized_query', j.optimized_query);",
                "    if (j.error !== undefined) pm.collectionVariables.set('last_error', String(j.error));",
                "    if (j.queries_remaining !== undefined) pm.environment.set('queries_remaining', String(j.queries_remaining));",
                "    console.log('status:', pm.response.code, '| error:', j.error, '| remaining:', j.queries_remaining);",
                "}"
            ]}
        }
    ],
    "variable": [
        {"key": "last_optimized_query", "value": ""},
        {"key": "last_summary", "value": ""}
    ]
}

environment = {
    "name": "SQL Optimizer - Local",
    "id": "sqlopt-local-env-0001",
    "values": [
        {"key": "base_url", "value": "http://127.0.0.1:8000", "enabled": True},
        {"key": "device_id", "value": "postman-device-1", "enabled": True},
        {"key": "queries_remaining", "value": "", "enabled": True},
        {"key": "db_host", "value": "", "enabled": True},
        {"key": "db_user", "value": "", "enabled": True},
        {"key": "db_password", "value": "", "enabled": True, "type": "secret"},
        {"key": "db_name", "value": "", "enabled": True}
    ],
    "_postman_variable_scope": "environment"
}

with open(COLL, "w", encoding="utf-8") as f:
    json.dump(collection, f, indent=2)
with open(ENVF, "w", encoding="utf-8") as f:
    json.dump(environment, f, indent=2)

print("wrote", COLL)
print("wrote", ENVF)
json.load(open(COLL, encoding="utf-8"))
json.load(open(ENVF, encoding="utf-8"))
print("both files are valid JSON")
