"""One-shot scan: exact text of dialect-hardcoded prompt lines. READ-ONLY."""
import io
import os
import re

BASE = r"c:\Team G\Jainil K task SQLopt\backend"
LLM = os.path.join(BASE, "llm-20260922T070723Z-1-001", "llm")

PAT = re.compile(r"mysql|force[_ ]?index|straight_join|use index", re.I)

FILES = [
    os.path.join(BASE, "sql_dialect.py"),
    os.path.join(LLM, "planner.py"),
    os.path.join(LLM, "rewriter.py"),
    os.path.join(LLM, "issue_finder.py"),
    os.path.join(BASE, "api.py"),
]

for path in FILES:
    name = os.path.basename(path)
    if not os.path.isfile(path):
        print(f"### {name}: MISSING")
        continue
    text = io.open(path, encoding="utf-8", errors="replace").read()
    lines = text.splitlines()
    print(f"### {name}  ({len(lines)} lines)")
    if name == "sql_dialect.py":
        print(text)
    else:
        hits = 0
        for i, line in enumerate(lines, 1):
            if PAT.search(line):
                hits += 1
                print(f"{i:4d}|{line.rstrip()[:150]!r}")
        if not hits:
            print("   (no dialect hits)")
    print()
