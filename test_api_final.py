import json
import requests
import time

BASE = "http://127.0.0.1:8000"

# Single, minimal test
body = {
    "query": "SELECT * FROM customers WHERE country='USA'",
    "agent_version": 1,
    "db_schema": "customers(id INT, name TEXT, country TEXT)",
    "schema_type": "MySQL",
    "scope": ["indexes", "SQL Optimization"]
}

print("=== Minimal agent_optimize test ===")
r = requests.post(f"{BASE}/agent_optimize", json=body, headers={"X-Device-ID": "test-minimal"})
print(f"Status: {r.status_code}")
d = r.json()
print(f"  optimized_query: {repr(d.get('optimized_query',''))}")
print(f"  actions: {d.get('actions')}")
print(f"  index_recommendations: {d.get('index_recommendations')}")
print(f"  summary: {d.get('summary')}")
print(f"  error: {d.get('error')}")
print(f"  queries_used: {d.get('queries_used')}, remaining: {d.get('queries_remaining')}")

# Wait for rate limit to clear, then test health
print("\n=== Health check ===")
time.sleep(2)
r2 = requests.get(f"{BASE}/health")
print(r2.json())