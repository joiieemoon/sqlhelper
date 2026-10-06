# backend/setup_test_db.py  — run once to prepare Aiven test data
import os
from dotenv import load_dotenv
import mysql.connector

# Load backend/.env (same pattern as api.py)
_env_file = os.path.join(os.path.dirname(__file__), ".env")
if os.path.isfile(_env_file):
    load_dotenv(_env_file)

DB_HOST     = os.getenv("DB_HOST",    "mysql-sql-optimizer-test01-sql-optimizer-test01.l.aivencloud.com")
DB_PORT     = int(os.getenv("DB_PORT", "17027"))
DB_USER     = os.getenv("DB_USER",    "avnadmin")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME     = os.getenv("DB_NAME",    "defaultdb")

assert DB_PASSWORD, "DB_PASSWORD is not set in backend/.env"

conn = mysql.connector.connect(
    host=DB_HOST,
    port=DB_PORT,
    user=DB_USER,
    password=DB_PASSWORD,
    database=DB_NAME,
    connection_timeout=15,
    ssl_disabled=False,
)
cur = conn.cursor()

# 1) Create a dedicated schema for this test project (clean separation)
TEST_DB = "sql_optimizer_test"

try:
    cur.execute(f"CREATE DATABASE {TEST_DB}")
    print(f"✓ Created database: {TEST_DB}")
except mysql.connector.Error as e:
    if e.errno == 1007:
        print(f"ℹ Database already exists: {TEST_DB}")
    else:
        raise

cur.execute(f"USE {TEST_DB}")

# 2) Customers table — common optimization candidate (composite index scenario)
cur.execute("""
CREATE TABLE IF NOT EXISTS customers (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(150),
    country VARCHAR(50),
    status VARCHAR(20),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_country (country),
    INDEX idx_status (status)
) ENGINE=InnoDB
""")
print("✓ Created table: customers")

# 3) Orders table — join partner
cur.execute("""
CREATE TABLE IF NOT EXISTS orders (
    id INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT NOT NULL,
    total DECIMAL(10,2),
    status VARCHAR(20),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_customer_id (customer_id),
    INDEX idx_total (total)
) ENGINE=InnoDB
""")
print("✓ Created table: orders")

# 4) Orders -> Customers foreign key (optional, but realistic)
try:
    cur.execute("""
    ALTER TABLE orders
      ADD CONSTRAINT fk_orders_customer
      FOREIGN KEY (customer_id) REFERENCES customers(id)
    """)
    print("✓ Added foreign key: orders -> customers")
except mysql.connector.Error:
    print("ℹ FK already exists or skipped (ok)")

# 5) Sample data — enough to make EXPLAIN ANALYZE meaningful
cur.executemany("""
INSERT INTO customers (name, email, country, status)
VALUES (%s, %s, %s, %s)
""", [
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
])
print("✓ Inserted 10 customers")

cur.executemany("""
INSERT INTO orders (customer_id, total, status, created_at)
VALUES (%s, %s, %s, %s)
""", [
    (1, 50.00,  "completed",  "2024-01-10 10:00:00"),
    (1, 120.00, "completed",  "2024-02-15 12:30:00"),
    (2, 30.00,  "pending",    "2024-03-01 09:00:00"),
    (3, 100.00, "completed",  "2024-03-10 14:20:00"),
    (5, 200.00, "completed",  "2024-04-05 08:15:00"),
    (5, 75.50,  "completed",  "2024-04-20 16:00:00"),
    (6, 90.00,  "pending",    "2024-05-01 11:00:00"),
    (10, 60.00, "completed",  "2024-05-10 13:45:00"),
    (10, 110.00,"completed",  "2024-06-01 10:30:00"),
    (4, 80.00,  "completed",  "2024-06-15 09:00:00"),
])
print("✓ Inserted 10 orders")

conn.commit()
cur.close()
conn.close()
print("DONE — test database ready at:", DB_HOST, DB_PORT, DB_NAME)
