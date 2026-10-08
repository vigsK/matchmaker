"""
MODE=compare  -  one invocation per query, run as a Distributed Map iteration.

For a single (RUN_ID, QUERY_ID):
  1. read the query item from s3://<bucket>/runs/<run_id>/queries.json
  2. run the PostgreSQL variant on the PG Aurora cluster
  3. run the MySQL variant on the MySQL Aurora cluster
  4. compare the two result sets order-independently (hash + row diff)
  5. write the verdict to s3://<bucket>/runs/<run_id>/results/<query_id>.json

The task exits 0 even when the result is "inconsistent" (that is a valid
business finding, not an infrastructure failure).  It exits non-zero only on a
genuine error (connection/SQL failure) so Step Functions can retry it.
"""
from __future__ import annotations

from .comparison import compare_result_sets
from .config import Config, get_creds
from .db import make_engine, run_query
from .s3util import get_json, put_json


def _find_item(items: list[dict], query_id: str) -> dict:
    for it in items:
        if it.get("query_id") == query_id:
            return it
    raise KeyError(f"query_id {query_id!r} not present in queries manifest")


def run(cfg: Config) -> dict:
    if not cfg.query_id:
        raise RuntimeError("MODE=compare requires QUERY_ID")
    print(f"[compare] run_id={cfg.run_id} query_id={cfg.query_id}")

    items = get_json(cfg.bucket, f"runs/{cfg.run_id}/queries.json")
    item = _find_item(items, cfg.query_id)

    result_key = f"runs/{cfg.run_id}/results/{cfg.query_id}.json"

    base = {
        "query_id": cfg.query_id,
        "run_id": cfg.run_id,
        "description": item.get("description", ""),
        "category": item.get("category", ""),
    }

    try:
        pg = make_engine(get_creds(cfg.pg_secret_arn, cfg.region))
        my = make_engine(get_creds(cfg.mysql_secret_arn, cfg.region))

        pg_cols, pg_rows, pg_secs = run_query(pg, item["postgres_sql"])
        my_cols, my_rows, my_secs = run_query(my, item["mysql_sql"])

        verdict = compare_result_sets(
            pg_rows, my_rows,
            a_label="postgres", b_label="mysql",
            diff_limit=cfg.diff_limit,
        )

        out = {
            **base,
            "status": "consistent" if verdict["consistent"] else "inconsistent",
            "postgres_columns": pg_cols,
            "mysql_columns": my_cols,
            "postgres_ms": round(pg_secs * 1000, 1),
            "mysql_ms": round(my_secs * 1000, 1),
            **verdict,
        }
        print(
            f"[compare] {cfg.query_id} -> {out['status']} "
            f"(pg={verdict['postgres_row_count']} rows / {out['postgres_ms']}ms, "
            f"mysql={verdict['mysql_row_count']} rows / {out['mysql_ms']}ms)"
        )
        put_json(cfg.bucket, result_key, out)
        return out

    except Exception as exc:  # noqa: BLE001
        out = {**base, "status": "error", "consistent": False, "error": str(exc)}
        # Persist the error too, so aggregate can report it, then re-raise so
        # Step Functions records the task as failed and applies its Retry policy.
        try:
            put_json(cfg.bucket, result_key, out)
        finally:
            print(f"[compare] ERROR on {cfg.query_id}: {exc}")
        raise
