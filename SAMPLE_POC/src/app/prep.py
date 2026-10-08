"""
MODE=prep  -  first state of the Step Functions workflow.

Reads the Excel workbook from S3, loads it into a pandas DataFrame (exactly the
"python reads, creates data frame" step from the brief), validates the required
columns, and writes a JSON array of query items to:

    s3://<bucket>/runs/<run_id>/queries.json

That JSON array is the ItemReader source for the Step Functions Distributed Map.
"""
from __future__ import annotations

import io

import pandas as pd

from .config import Config
from .s3util import get_bytes, put_json

REQUIRED_COLUMNS = ["query_id", "postgres_sql", "mysql_sql"]
OPTIONAL_COLUMNS = ["description", "category"]


def run(cfg: Config) -> dict:
    print(f"[prep] run_id={cfg.run_id} reading s3://{cfg.bucket}/{cfg.excel_key}")
    raw = get_bytes(cfg.bucket, cfg.excel_key)
    df = pd.read_excel(io.BytesIO(raw), engine="openpyxl", dtype=str)
    df.columns = [c.strip() for c in df.columns]

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            f"excel is missing required columns {missing}; found {list(df.columns)}"
        )

    items = []
    for idx, row in df.iterrows():
        qid = (row.get("query_id") or "").strip()
        pg = (row.get("postgres_sql") or "").strip()
        my = (row.get("mysql_sql") or "").strip()
        if not qid or not pg or not my:
            print(f"[prep] skipping incomplete row {idx}: {dict(row)}")
            continue
        items.append(
            {
                "query_id": qid,
                "description": (row.get("description") or "").strip()
                if "description" in df.columns else "",
                "category": (row.get("category") or "").strip()
                if "category" in df.columns else "",
                "postgres_sql": pg,
                "mysql_sql": my,
            }
        )

    if not items:
        raise ValueError("no valid query rows found in the excel workbook")

    key = f"runs/{cfg.run_id}/queries.json"
    put_json(cfg.bucket, key, items)
    print(f"[prep] wrote {len(items)} query items to s3://{cfg.bucket}/{key}")

    return {"run_id": cfg.run_id, "bucket": cfg.bucket,
            "items_key": key, "count": len(items)}
