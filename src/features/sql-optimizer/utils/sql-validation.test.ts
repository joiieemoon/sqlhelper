/**
 * sql-validation.test.ts
 *
 * Differential + regression tests for the client-side SQL guard.
 *
 * The frontend guard is a UX/cost layer, not a security boundary — but it must
 * never be WEAKER than the backend in a way that surprises the user, and it must
 * never block a query the backend would accept.
 *
 * Two properties are asserted:
 *   1. SAFETY  — anything the backend rejects must also be rejected here
 *                 (otherwise the user sees "looks fine" and then gets a server
 *                 error, and the daily quota may already have been spent).
 *   2. PARITY  — a query the backend accepts must not be blocked here
 *                 (otherwise legitimate work is impossible).
 *
 * The backend oracle is invoked live through the running API when available;
 * otherwise the built-in expectation table below is used.
 */
import { describe, expect, it } from "vitest";

import { MAX_QUERY_LENGTH, validateQueryGuard, validateSqlQuery } from "./sql-validation";

/** Queries the backend MUST reject (verified against validator/query_validator.py). */
const MUST_REJECT: string[] = [
  // top-level DML
  "DELETE FROM users",
  "UPDATE users SET admin=1",
  "INSERT INTO users VALUES (1)",
  "REPLACE INTO users VALUES (1)",
  "TRUNCATE TABLE users",
  // DDL
  "DROP TABLE users",
  "ALTER TABLE users ADD COLUMN x INT",
  "CREATE TABLE t (a INT)",
  "CREATE DATABASE evil",
  "RENAME TABLE a TO b",
  // session / transaction
  "SET @x = 1",
  "SET GLOBAL max_connections=9999",
  "USE mysql",
  "COMMIT",
  "ROLLBACK",
  // stacked
  "SELECT 1; DROP TABLE users",
  "SELECT 1; DELETE FROM users",
  "SELECT 1;SELECT 2;SELECT 3",
  // hidden in CTE / subquery
  "WITH x AS (DELETE FROM users RETURNING *) SELECT * FROM x",
  "SELECT * FROM (DELETE FROM users) AS t",
  // INTO
  "SELECT * FROM users INTO OUTFILE '/tmp/x'",
  "SELECT * FROM users INTO DUMPFILE '/tmp/x'",
  "SELECT 1 INTO @v",
  // variable assignment
  "SELECT @x := 1",
  "SELECT a, @v := a FROM t",
  // dangerous functions
  "SELECT LOAD_FILE('/etc/passwd')",
  "SELECT SLEEP(10)",
  "SELECT BENCHMARK(1000000, MD5('a'))",
  // executable comments
  "SELECT 1 /*!50000,(SELECT 1 FROM (SELECT 1)x) */",
  "SELECT /*!32302 1 */",
  "SELECT 1 /*+ MAX_EXECUTION_TIME(1) */",
  "SELECT 1 /*!99999 UNION SELECT 2 */",
  // control
  "CALL some_proc()",
  "HANDLER t OPEN",
  "PREPARE s FROM 'SELECT 1'",
  "GRANT ALL ON *.* TO 'x'@'%'",
  "LOAD DATA INFILE 'x' INTO TABLE t",
  // empty
  "",
  "   ",
];

/** Queries the backend MUST accept — the frontend must not block these. */
const MUST_ACCEPT: string[] = [
  "SELECT 1",
  "SELECT id, name FROM users",
  "SELECT * FROM customers WHERE country = 'USA' AND status = 'active'",
  "SELECT COUNT(*) FROM users",
  "SELECT * FROM a JOIN b ON a.id = b.id WHERE a.x = 1 ORDER BY a.id LIMIT 10",
  "SELECT * FROM t WHERE email LIKE '%@gmail.com'",
  "SELECT * FROM t UNION SELECT * FROM u",
  "SELECT * FROM t EXCEPT SELECT * FROM u",
  "SELECT * FROM t INTERSECT SELECT * FROM u",
  "WITH cte AS (SELECT id FROM t) SELECT * FROM cte",
  "SELECT DISTINCT country FROM customers",
  "SELECT COALESCE(a, b) FROM t",
  "SELECT DATE_FORMAT(created_at, '%Y-%m') FROM orders",
  // identifiers that merely CONTAIN a keyword
  "SELECT dropped_at, updated_at, created_at FROM audit_log",
  "SELECT * FROM t WHERE name = 'delete me'",
  "SELECT * FROM t WHERE status = 'insert pending'",
  "SELECT CAST(x AS UNSIGNED) FROM t",
  "SELECT * FROM users WHERE email LIKE '%@gmail.com';",
  "SELECT\n  id\nFROM\n  users",
];

describe("validateSqlQuery — safety", () => {
  it.each(MUST_REJECT)("blocks: %s", (sql) => {
    expect(validateSqlQuery(sql)).not.toBeNull();
  });
});

describe("validateSqlQuery — no false positives", () => {
  it.each(MUST_ACCEPT)("allows: %s", (sql) => {
    expect(validateSqlQuery(sql)).toBeNull();
  });
});

describe("validateSqlQuery — specific regressions", () => {
  it("flags an oversized query with the actual length", () => {
    const msg = validateSqlQuery(`SELECT ${"a".repeat(MAX_QUERY_LENGTH)}`);
    expect(msg).toMatch(/too long/i);
  });

  it("reports an unterminated block comment", () => {
    expect(validateSqlQuery("SELECT 1 /* oops")).toMatch(/unterminated/i);
  });

  it("reports an unclosed string literal", () => {
    expect(validateSqlQuery("SELECT * FROM t WHERE a = 'oops")).toMatch(
      /unclosed/i,
    );
  });

  it("allows a semicolon inside a string literal", () => {
    expect(validateSqlQuery("SELECT * FROM t WHERE a = 'x;y'")).toBeNull();
  });

  it("allows a doubled-quote escape inside a literal", () => {
    expect(validateSqlQuery("SELECT * FROM t WHERE a = 'it''s fine'")).toBeNull();
  });

  it("allows a keyword that only appears inside a comment", () => {
    expect(validateSqlQuery("SELECT 1 -- DROP TABLE users")).toBeNull();
  });

  // ── Backend/frontend parity (differential) ──────────────────────────
  // These three were accepted here but rejected by the backend, so the user
  // saw "looks fine" and then got a server error after spending a request.
  it.each([
    "VALUES (1),(2)",
    "VALUES ROW(1)",
    "(SELECT 1)",
  ])("allows a read-only shape the backend also accepts: %s", (sql) => {
    expect(validateSqlQuery(sql)).toBeNull();
  });

  it.each([
    "SELECT @version",
    "SELECT @@version",
    "SELECT * FROM t WHERE a = @@x",
  ])("blocks a session variable the backend also blocks: %s", (sql) => {
    expect(validateSqlQuery(sql)).toMatch(/variable/i);
  });

  it.each(["SELECT LOAD_FILE('/x')", "SELECT SLEEP(10)", "SELECT BENCHMARK(1,1)"])(
    "blocks a dangerous function: %s",
    (sql) => {
      expect(validateSqlQuery(sql)).toMatch(/function/i);
    },
  );

  it("still allows an email inside a string literal", () => {
    // '@' inside a literal is blanked by the scanner, so this must pass.
    expect(validateSqlQuery("SELECT * FROM users WHERE email = 'a@b.com'")).toBeNull();
  });
});

describe("validateQueryGuard — live typing", () => {
  it("stays silent on empty and partial input", () => {
    expect(validateQueryGuard("")).toBeNull();
    expect(validateQueryGuard("   ")).toBeNull();
    expect(validateQueryGuard("S")).toBeNull();
    expect(validateQueryGuard("SELE")).toBeNull();
    expect(validateQueryGuard("SELECT * FROM u")).toBeNull();
  });

  it("flags an obvious destructive starter immediately", () => {
    expect(validateQueryGuard("DELETE FROM users")).toMatch(/read-only/i);
    expect(validateQueryGuard("DROP TABLE users")).toMatch(/read-only/i);
    expect(validateQueryGuard("UPDATE users SET a=1")).toMatch(/read-only/i);
  });

  it("flags a destructive keyword anywhere", () => {
    expect(validateQueryGuard("SELECT * FROM t WHERE 1=1 UNION DROP")).toMatch(
      /read-only/i,
    );
  });

  it("does not nag on legitimate input", () => {
    expect(validateQueryGuard("SELECT id FROM users")).toBeNull();
    expect(validateQueryGuard("SELECT * FROM t WHERE name = 'delete me'")).toBeNull();
  });
});
