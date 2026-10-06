import sqlglot

tests = [
    "SELECT REPLACE(name,'a','b') FROM t",
    "SELECT * FROM t FOR UPDATE",
    "SELECT * FROM t INTO OUTFILE 'x'",
    "WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d",
    "DELETE FROM t",
    "UPDATE t SET a=1",
    "INSERT INTO t VALUES (1)",
    "DROP TABLE t",
    "TRUNCATE TABLE t",
    "ALTER TABLE t ADD c INT",
    "CREATE TABLE x (a INT)",
    "SELECT 1; DELETE FROM t",
    "EXPLAIN SELECT 1",
]

for t in tests:
    try:
        stmts = [s for s in sqlglot.parse(t, read="mysql") if s is not None]
        kinds = []
        for s in stmts:
            nodes = sorted({type(n).__name__ for n in s.walk()})
            kinds.append(type(s).__name__ + " | " + ",".join(nodes))
        print(repr(t))
        for k in kinds:
            print("    ", k)
    except Exception as e:
        print(repr(t), "-> ParseError", type(e).__name__)
