import os, sys
import mysql.connector

host = os.environ["PROBE_HOST"]
port = int(os.environ["PROBE_PORT"])
user = os.environ["PROBE_USER"]
pwd  = os.environ["PROBE_PASS"]
db   = os.environ["PROBE_DB"]

def show(label, fn):
    try:
        print(label, "->", fn())
    except Exception as e:
        print(label, "!! ERROR:", type(e).__name__, e)

try:
    conn = mysql.connector.connect(
        host=host, port=port, user=user, password=pwd, database=db,
        connection_timeout=15, ssl_disabled=False,
        ssl_verify_cert=False, ssl_verify_identity=False,
    )
except Exception as e:
    print("CONNECT FAILED:", type(e).__name__, e)
    sys.exit(1)

cur = conn.cursor()
show("SERVER/COLLATION", lambda: (cur.execute("SELECT VERSION(), @@lower_case_table_names, @@collation_server"), cur.fetchall())[1])
show("DATABASE()", lambda: (cur.execute("SELECT DATABASE()"), cur.fetchall())[1])
show("SHOW DATABASES", lambda: (cur.execute("SHOW DATABASES"), cur.fetchall())[1])
show("TABLES IN DATABASE()", lambda: (cur.execute("SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME"), cur.fetchall())[1])
show("ANY SCHEMA LIKE %ustomer%", lambda: (cur.execute("SELECT TABLE_SCHEMA, TABLE_NAME FROM information_schema.TABLES WHERE TABLE_NAME LIKE '%ustomer%'"), cur.fetchall())[1])
show("COUNT(*) FROM Customer", lambda: (cur.execute("SELECT COUNT(*) FROM Customer"), cur.fetchall())[1])
show("COLUMNS Customer in DATABASE()", lambda: (cur.execute("SELECT COLUMN_NAME, COLUMN_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s ORDER BY ORDINAL_POSITION", ("Customer",)), cur.fetchall())[1])

cur.close(); conn.close()
