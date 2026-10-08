#!/usr/bin/env python3
"""
Local end-to-end validation WITHOUT any AWS resources.

Spins the same application logic against local PostgreSQL + MySQL containers:
seeds identical data into both, runs all 20 query pairs on each engine, and runs
the real order-independent comparison. Proves the SQL dialects are logically
equivalent before spending a cent in the cloud.

Expects local containers:
  postgres: localhost:55432  db=appdb user=postgres pass=pass
  mysql:    localhost:33060  db=appdb user=root     pass=pass
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from app.config import DbCreds  # noqa: E402
from app.db import make_engine, wait_for_db, run_query  # noqa: E402
from app.seed import _generate, _load  # noqa: E402
from app.queries import QUERY_PAIRS  # noqa: E402
from app.comparison import compare_result_sets  # noqa: E402

ROW_COUNT = int(os.environ.get("ROW_COUNT", "3000"))

PG = DbCreds("postgres", "127.0.0.1", int(os.environ.get("PG_PORT", "55432")),
             "postgres", "pass", "appdb")
MY = DbCreds("mysql", "127.0.0.1", int(os.environ.get("MYSQL_PORT", "33061")),
             "root", "pass", "appdb")


def main() -> int:
    pg = make_engine(PG)
    my = make_engine(MY)
    print("Waiting for local databases...")
    wait_for_db(pg, attempts=30, delay=3)
    wait_for_db(my, attempts=30, delay=3)

    print(f"Seeding identical data (row_count={ROW_COUNT})...")
    customers, products, orders = _generate(ROW_COUNT)
    _load(pg, "postgres", customers, products, orders)
    _load(my, "mysql", customers, products, orders)

    print("\nRunning 20 query pairs:\n" + "-" * 78)
    failures = []
    for q in QUERY_PAIRS:
        _, pg_rows, pg_s = run_query(pg, q["postgres_sql"])
        _, my_rows, my_s = run_query(my, q["mysql_sql"])
        verdict = compare_result_sets(pg_rows, my_rows)
        ok = verdict["consistent"]
        flag = "PASS" if ok else "FAIL"
        print(f"[{flag}] {q['query_id']:<32} "
              f"pg={verdict['postgres_row_count']:>4} my={verdict['mysql_row_count']:>4} "
              f"({pg_s*1000:6.1f}ms / {my_s*1000:6.1f}ms)")
        if not ok:
            failures.append((q["query_id"], verdict))

    print("-" * 78)
    if failures:
        print(f"\n{len(failures)} INCONSISTENT:")
        for qid, v in failures:
            print(f"  {qid}: {v.get('diff')}")
        return 1
    print(f"\nALL {len(QUERY_PAIRS)} QUERY PAIRS CONSISTENT across PostgreSQL and MySQL.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
