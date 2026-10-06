"""Throwaway: prove FORCE INDEX is invalid in PostgreSQL."""
import os
import psycopg2

c = psycopg2.connect(
    host="pg-285a382b-sql-helper-testing2.j.aivencloud.com", port=21378,
    user="avnadmin", password=os.environ["TEST_PG_PW"], dbname="defaultdb",
    sslmode="require", connect_timeout=15,
)
c.autocommit = True
cur = c.cursor()

produced = (
    "SELECT c.id, c.name, c.city FROM opt_customers c "
    "FORCE INDEX (PRIMARY) JOIN opt_orders o ON c.id = o.customer_id "
    "WHERE o.status = 'completed';"
)
print("=== executing the query the backend returned ===")
print(produced)
print()
try:
    cur.execute(produced)
    print("RESULT:", cur.fetchall()[:5])
except Exception as exc:
    print("*** EXECUTION FAILED ***")
    print(type(exc).__name__, "|", getattr(exc, "pgcode", None))
    print(str(exc).strip()[:400])

print()
print("=== same query WITHOUT the MySQL hint, but with ::text difference ===")
ok = (
    "SELECT c.id, c.name, c.city FROM opt_customers c "
    "JOIN opt_orders o ON c.id = o.customer_id WHERE o.status = 'completed';"
)
try:
    cur.execute(ok)
    print("rows:", len(cur.fetchall()), "-> valid PostgreSQL")
except Exception as exc:
    print("FAILED:", str(exc).strip()[:200])
c.close()
