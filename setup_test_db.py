# -*- coding: utf-8 -*-
"""
setup_test_db.py â€” run ONCE to prepare a clean test MySQL database on Aiven.

Creates:
  - database sql_optimizer_test
  - table customers  (index on country, index on status)
  - table orders     (index on customer_id, index on total)
  - foreign key orders.customer_id -> customers.id
  - 10 sample customers + 10 sample orders

Reads connection info from backend/.env (DB_HOST, DB_PORT, DB_USER,
DB_PASSWORD, DB_NAME).  DB_PASSWORD must be set in .env (gitignored).
"""

from __future__ import annotations
import os
from dotenv import load_dotenv
import mysql.connector
from mysql.connector import errorcode

ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.isfile(ENV_FILE):
    load_dotenv(ENV_FILE)

DB_HOST     = os.getenv("DB_HOST",    "mysql-sql-optimizer-test01-sql-optimizer-test01.l.aivencloud.com")
DB_PORT     = int(os.getenv("DB_PORT", "17027"))
DB_USER     = os.getenv("DB_USER",    "avnadmin")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME     = os.getenv("DB_NAME",    "defaultdb")

if not DB_PASSWORD:
    raise SystemExit("ERROR: DB_PASSWORD is not set in backend/.env (add it; do NOT commit).")


def connect(base_db: str = DB_NAME):
    return mysql.connector.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD,
        database=base_db, connection_timeout=20, ssl_disabled=False,
    )


def main() -> None:
    print("Connecting to Aiven MySQL ...")
    conn = connect()
    cur = conn.cursor()

    TEST_DB = "sql_optimizer_test"
    try:
        cur.execute(f"CREATE DATABASE {TEST_DB} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        print(f"Created database: {TEST_DB}")
    except mysql.connector.Error as e:
        if e.errno == errorcode.ER_DB_CREATE_EXISTS:
            print(f"Info: database already exists: {TEST_DB}")
        else:
            raise

    cur.execute(f"USE {TEST_DB}")
    try:
        cur.execute("""
            CREATE TABLE customers (
                id         INT AUTO_INCREMENT PRIMARY KEY,
                name       VARCHAR(100)     NOT NULL,
                email      VARCHAR(150),
                country    VARCHAR(50),
                status     VARCHAR(20),
                created_at DATETIME         DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_country (country),
                INDEX idx_status (status)
            ) ENGINE=InnoDB
        """)
        print("âœ“ Created table: customers")
    except mysql.connector.Error as e:
        if e.errno == errorcode.ER_TABLE_EXISTS_ERROR:
            print("â„¹ Table customers already exists")
        else:
            raise

    try:
        cur.execute("""
            CREATE TABLE orders (
                id          INT AUTO_INCREMENT PRIMARY KEY,
                customer_id INT           NOT NULL,
                total       DECIMAL(10,2),
                status      VARCHAR(20),
                created_at  DATETIME      DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_customer_id (customer_id),
                INDEX idx_total (total)
            ) ENGINE=InnoDB
        """)
        print("âœ“ Created table: orders")
    except mysql.connector.Error as e:
        if e.errno == errorcode.ER_TABLE_EXISTS_ERROR:
            print("â„¹ Table orders already exists")
        else:
            raise

    try:
        cur.execute("SHOW CREATE TABLE orders")
        create_sql = cur.fetchone()[1]
        if "fk_orders_customer" not in create_sql:
            try:
                cur.execute("""
                    ALTER TABLE orders
                      ADD CONSTRAINT fk_orders_customer
                      FOREIGN KEY (customer_id) REFERENCES customers(id)
                """)
                print("âœ“ Added FK: orders.customer_id -> customers.id")
            except mysql.connector.Error as e:
                print(f"â„¹ FK skipped: {e}")
        else:
            print("â„¹ FK fk_orders_customer already exists")
    except mysql.connector.Error as e:
        print(f"â„¹ FK check skipped: {e}")

    customers = [
        ("Alice",  "alice@example.com",  "USA",    "active"),
        ("Bob",    "bob@example.com",    "USA",    "active"),
        ("Carol",  "carol@example.com",  "UK",     "active"),
        ("Dave",   "dave@example.com",   "USA",    "inactive"),
        ("Eve",    "eve@example.com",    "USA",    "active"),
        ("Frank",  "frank@example.com",  "Canada", "active"),
        ("Grace",  "grace@example.com",  "USA",    "inactive"),
        ("Hank",   "hank@example.com",   "UK",     "active"),
        ("Ivy",    "ivy@example.com",    "USA",    "active"),
        ("Jack",   "jack@example.com",   "USA",    "active"),
    ]
    cur.executemany(
        "INSERT INTO customers (name, email, country, status) VALUES (%s,%s,%s,%s)",
        customers,
    )
    print(f"âœ“ Inserted {len(customers)} customers")

    orders = [
        (1,  50.00,  "completed", "2024-01-10 10:00:00"),
        (1,  120.00, "completed", "2024-02-15 12:30:00"),
        (2,  30.00,  "pending",   "2024-03-01 09:00:00"),
        (3,  100.00, "completed", "2024-03-10 14:20:00"),
        (5,  200.00, "completed", "2024-04-05 08:15:00"),
        (5,  75.50,  "completed", "2024-04-20 16:00:00"),
        (6,  90.00,  "pending",   "2024-05-01 11:00:00"),
        (10, 60.00,  "completed", "2024-05-10 13:45:00"),
        (10, 110.00, "completed", "2024-06-01 10:30:00"),
        (4,  80.00,  "completed", "2024-06-15 09:00:00"),
    ]
    cur.executemany(
        "INSERT INTO orders (customer_id, total, status, created_at) VALUES (%s,%s,%s,%s)",
        orders,
    )
    print(f"âœ“ Inserted {len(orders)} orders")

    conn.commit()
    cur.close()
    conn.close()
    print("\nDONE â€” test DB ready:")
    print(f"  host     : {DB_HOST}")
    print(f"  port     : {DB_PORT}")
    print(f"  database : {TEST_DB}")
    print(f"  user     : {DB_USER}")
    print("  next: point /db_optimize_test -> sql_optimizer_test")


if __name__ == "__main__":
    main()