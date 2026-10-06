#!/usr/bin/env python
"""
view_trace.py — read and filter the API trace log.

Run:  python view_trace.py                 last 20 records
      python view_trace.py -n 50           last 50
      python view_trace.py -f json         raw JSON for the last record
      python view_trace.py -f full         last record, fully expanded
      python view_trace.py --summary       counts by endpoint / outcome
      python view_trace.py --errors        only failures
      python view_trace.py --path /db_optimize_full
      python view_trace.py --since 1h      last hour
      python view_trace.py --watch         live tail
      python view_trace.py --check         verify no password leaked
"""

import argparse
import json
import os
import sys
import time
from collections import Counter

TRACE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "trace", "api_trace.jsonl"
)

SECRET_MARKERS = ("AVNS_", "PRIVATE KEY", "AIza", "ya29.")


def load(path=TRACE_FILE):
    if not os.path.exists(path):
        print(f"no trace file yet at {path}")
        print("start the server and make a request, then run this again")
        return []
    records = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _since_ok(rec, seconds):
    if not seconds:
        return True
    try:
        ts = time.mktime(time.strptime(rec["ts"][:19], "%Y-%m-%dT%H:%M:%S"))
        return (time.time() - ts) <= seconds
    except Exception:
        return True


def print_one(rec, full=False):
    print("=" * 78)
    print(f"{rec['ts']}  {rec.get('req_id')}  {rec.get('method')} {rec.get('path')}")
    print(
        f"  status={rec.get('status')}  outcome={rec.get('outcome')}  "
        f"{rec.get('elapsed_ms')}ms  device={rec.get('device_id')}"
    )
    db = rec.get("db")
    if db:
        print(f"  db: {db.get('user')}@{db.get('host')}:{db.get('port')}/{db.get('database')}")
    if rec.get("query"):
        print(f"  query: {str(rec['query'])[:300]}")
    resp = rec.get("response") or {}
    if resp.get("optimized_query"):
        print(f"  optimized: {str(resp['optimized_query'])[:300]}")
    if resp.get("query_changed") is not None:
        print(f"  changed: {resp['query_changed']}  "
              f"actions={len(resp.get('actions') or [])}  "
              f"index_recs={len(resp.get('index_recommendations') or [])}")
    if resp.get("error"):
        print(f"  error: {str(resp['error'])[:220]}")
    if rec.get("limit") is not None:
        print(f"  quota: {rec.get('queries_used')}/{rec.get('limit')} used")
    if full:
        print("-" * 78)
        print(json.dumps(rec, indent=2, default=str))


def summary(records):
    print(f"total records: {len(records)}\n")
    print("by endpoint:")
    for path, n in Counter(r.get("path") for r in records).most_common():
        print(f"  {n:>4}  {path}")
    print("\nby outcome:")
    for out, n in Counter(r.get("outcome") for r in records).most_common():
        print(f"  {n:>4}  {out}")
    print("\nby status:")
    for st, n in Counter(r.get("status") for r in records).most_common():
        print(f"  {n:>4}  HTTP {st}")
    times = [r.get("elapsed_ms") or 0 for r in records]
    if times:
        print(f"\nlatency: avg={sum(times)//len(times)}ms  max={max(times)}ms")
    changed = [r for r in records
               if (r.get("response") or {}).get("query_changed") is True]
    ok = [r for r in records if r.get("outcome") == "ok"]
    print("\noptimizer accuracy signal:")
    print(f"  successful runs       : {len(ok)}")
    print(f"  query actually changed: {len(changed)}")
    if ok:
        print(f"  change rate           : {len(changed)*100//len(ok)}%")


def check_secrets(records):
    """Fail loudly if a credential ever reached the trace file."""
    leaks = []
    for rec in records:
        blob = json.dumps(rec, default=str)
        for marker in SECRET_MARKERS:
            if marker in blob:
                leaks.append((rec.get("req_id"), marker))
    if leaks:
        print("!! POSSIBLE SECRET LEAK")
        for req_id, marker in leaks[:20]:
            print(f"   {req_id}: {marker}")
        return 1
    print(f"OK - no credentials in {len(records)} records ({TRACE_FILE})")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", "--count", type=int, default=20)
    ap.add_argument("-f", "--format", choices=["short", "json", "full"], default="short")
    ap.add_argument("--id", help="show this req_id")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--errors", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--path")
    ap.add_argument("--since", help="e.g. 30m, 2h, 1d")
    ap.add_argument("--watch", action="store_true")
    args = ap.parse_args()

    if args.watch:
        print(f"tailing {TRACE_FILE} - Ctrl+C to stop")
        try:
            with open(TRACE_FILE, encoding="utf-8") as fh:
                fh.seek(0, os.SEEK_END)
                while True:
                    line = fh.readline()
                    if line.strip():
                        print_one(json.loads(line))
                    else:
                        time.sleep(0.5)
        except KeyboardInterrupt:
            print("\nstopped")
        return 0

    records = load()
    if not records:
        return 0

    if args.check:
        return check_secrets(records)

    secs = 0
    if args.since:
        mult = {"m": 60, "h": 3600, "d": 86400}
        secs = int(args.since[:-1]) * mult.get(args.since[-1].lower(), 1)

    if args.path:
        records = [r for r in records if r.get("path") == args.path]
    if secs:
        records = [r for r in records if _since_ok(r, secs)]
    if args.errors:
        records = [r for r in records if r.get("status") != 200]

    if args.summary:
        summary(records)
        return 0

    if args.id:
        for r in records:
            if r.get("req_id") == args.id:
                print(json.dumps(r, indent=2, default=str))
                return 0
        print(f"req_id {args.id} not found")
        return 1

    tail = records[-args.count:]
    if args.format == "json":
        print(json.dumps(tail[-1] if tail else None, indent=2, default=str))
    else:
        for r in tail:
            print_one(r, full=(args.format == "full"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
