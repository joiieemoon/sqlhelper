# Tiny live probe: print the raw mysql-connector EXPLAIN ANALYZE column shape.
# Reads password from backend/.env like the server does.
import os
import sys

pp = os.path.dirname(os.path.abspath(__file__))
sys.path[0:0] = [
    pp,
    os.path.join(pp, "llm-20260922T070723Z-1-001"),
    os.path.join(pp, "db_less_optimizer-20260922T070953Z-1-001"),
    os.path.join(pp, "db_less_optimizer-20260922T070953Z-1-001", "goflags"),
    os.path.join(pp, "validator-20260922T070723Z-1-001"),
    os.path.join(pp, "utils-20260922T070723Z-1-001"),
]

from dotenv import load_dotenv

load_dotenv(os.path.join(pp, ".env"))

import db_utils
import mysql.connector

cfg = {
    "host": os.environ.get("DB_HOST", "mysql-sql-optimizer-test01-sql-optimizer-test01.l.aivencloud.com"),
    "port": int(os.environ.get("DB_PORT", "17027")),
    "user": os.environ.get("DB_USER", "avnadmin"),
    "password": os.environ.get("DB_PASSWORD", "placeholder"),
    "database": os.environ.get("DB_NAME", "sql_optimizer_test"),
    "ssl_verify": os.environ.get("DB_SSL_VERIFY", "0").lower() in ("1", "true", "yes"),
}

query = "SELECT id, name FROM customers WHERE country = 'USA' AND status = 'active'"

conn = db_utils.connect_mysql(cfg)
cur = conn.cursor()
try:
    cur.execute("EXPLAIN ANALYZE " + query)
    print("columns =", [d[0] for d in cur.description])
    for row in cur.fetchall():
        print(row)
finally:
    cur.close()
    conn.close()



