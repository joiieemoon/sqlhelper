import inspect, re, os
os.chdir(r"c:\Team G\Jainil K task SQLopt\backend")

print("##### sql_dialect.py PUBLIC API #####")
import sql_dialect
for n in sorted(dir(sql_dialect)):
    if n.startswith("_"):
        continue
    o = getattr(sql_dialect, n)
    if callable(o):
        try:
            print("  FUNC", n, inspect.signature(o))
        except Exception:
            print("  FUNC", n)
    else:
        print("  DATA", n, "=", repr(o)[:150])

print()
print("##### api.py: anchors #####")
src = open("api.py", encoding="utf-8").read().splitlines()
for i, l in enumerate(src, 1):
    if re.search(r"optimized_query|engine|rewriter_llm|connect_mysql|connect_db|sanitiz", l):
        print(i, "|", l.strip()[:150])
