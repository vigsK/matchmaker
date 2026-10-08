# Comparison Logic & Output — Deep-Dive Analysis

This document is a deep dive into the two things at the heart of the project:

1. **How the app decides two databases agree** — the order-independent
   result-set comparison algorithm (`src/app/comparison.py`, `src/app/compare.py`).
2. **What the run actually produces** — every output artifact, its exact JSON
   shape, and where to find it (`src/app/prep.py`, `compare.py`, `aggregate.py`,
   and the Step Functions `ResultWriter`).

---

# Part 1 — The Comparison Logic

## 1.1 The premise it rests on

`seed.py` inserts the **exact same Python-generated rows** into *both* engines
(PostgreSQL standing in for Oracle, MySQL for Snowflake), so the two databases
start **byte-for-byte equivalent**. Therefore, for each of the 20 query pairs,
both engines **must** return the same *set* of rows.

> Any difference the comparison detects is a **real divergence** — a dialect bug,
> a precision mismatch, or a logic error — never seed drift.

The algorithm compares result sets as **multisets of rows**, ignoring the order
the database happened to return them in. This "order-independent" property is the
single most important design decision.

## 1.2 The five-step pipeline (per query)

`compare.run()` (`compare.py:30`) drives one query pair:

```
1. run postgres_sql on PG    →  pg_rows      (db.run_query → fetchall)
2. run mysql_sql   on MySQL  →  my_rows
3. compare_result_sets(pg_rows, my_rows)      ← the algorithm
4. build verdict dict
5. write runs/<run_id>/results/<query_id>.json
```

Steps 1–2 use `db.run_query` (`db.py:77`), returning `(columns, rows, elapsed)`.
Step 3 — `compare_result_sets` (`comparison.py:80`) — is where the logic lives.

## 1.3 Step A — Canonicalization (the hard part)

**Problem:** engines represent the *same value* differently:

| Same logical value | PostgreSQL emits | MySQL emits |
|---|---|---|
| one hundred | `100` | `1E+2` |
| money | `152340.5` | `152340.50` |
| boolean true | `True` | `1` |
| timestamp | `2024-01-01 00:00:00` | `2024-01-01 00:00:00.000` |
| NULL | `None` | `None` (needs a stable token) |

**Solution:** before any comparison, every cell is normalized to a canonical
string by `canon_cell` (`comparison.py:47`):

| DB value type | Canonical form | Why |
|---|---|---|
| `None` (SQL NULL) | `"∅"` | unambiguous token, distinct from empty string |
| `bool` | `"1"` / `"0"` | collapse PG `True` vs MySQL `1` |
| `int` | `str(value)` | exact |
| `Decimal` / `float` | `_canon_number()` | **critical — see below** |
| `datetime` | ISO, microseconds dropped | engines differ on sub-second precision |
| `date` | ISO | |
| `timedelta` | `str()` | MySQL TIME types |
| `bytes` | `.hex()` | stable binary representation |
| other | `str(value).strip()` | trim whitespace differences |

### `_canon_number` — the false-positive killer (`comparison.py:30`)

```python
dec  = Decimal(str(value)).quantize(Decimal("1.000000"))  # round to 6 dp
text = format(dec, "f")                                    # no scientific notation
text = text.rstrip("0").rstrip(".")                        # drop trailing zeros
```

Effect: `152340.5`, `152340.50`, `152340.500000`, and `1.5234055E5` **all**
canonicalize to `"152340.5"`.

- **Round to 6 dp** → collapses tiny floating-point / engine precision noise
  (e.g. an `AVG` computed slightly differently) without hiding real differences.
- **No scientific notation** → `1E+2` and `100` compare equal.
- **Strip trailing zeros** → handles `DECIMAL(10,2)` vs `NUMERIC` scale diffs.

A whole row becomes one string via `canon_row` (`comparison.py:68`):
`canon_cell(c) for c in row`, joined by a separator. **One row → one canonical
string.**

## 1.4 Step B — Order-independent equality (sort + fingerprint)

```python
a_canon  = [canon_row(r) for r in a_rows]   # list of canonical row-strings
b_canon  = [canon_row(r) for r in b_rows]
a_sorted = sorted(a_canon)                   # ← erases row-order differences
b_sorted = sorted(b_canon)
a_hash   = _fingerprint(a_sorted)            # SHA-256 over sorted rows
b_hash   = _fingerprint(b_sorted)
consistent = (a_hash == b_hash) and (a_count == b_count)
```

- **`sorted()`** makes the comparison order-independent: same rows in a different
  order sort into alignment. (Many queries also use `ORDER BY`, but correctness
  does not *depend* on it.)
- **`_fingerprint` (`comparison.py:72`)** SHA-256-hashes the sorted rows into one
  64-char digest per side — a cheap, storable proxy for the entire result set.
- **Verdict:** `consistent` ⇔ **hashes equal AND row counts equal**. The
  row-count check is belt-and-suspenders (equal hashes already imply equal
  counts) but makes intent explicit.

Both digests are stored (`postgres_hash`, `mysql_hash`) for audit — you can
confirm a match without re-running.

## 1.5 Step C — On mismatch, locate the divergence (multiset diff)

"Not equal" isn't actionable. When inconsistent, a **multiset difference** via
`Counter` (`comparison.py:114`) shows *which* rows differ:

```python
a_counter = Counter(a_canon)        # row-string → occurrence count
b_counter = Counter(b_canon)
only_a = a_counter - b_counter      # rows on PG missing/short on MySQL
only_b = b_counter - a_counter      # rows on MySQL missing/short on PG
```

`Counter` subtraction is **multiset-aware**: a row appearing 3× in PG but 2× in
MySQL leaves 1 copy in `only_a` — so it catches **duplicate-count drift**, not
just missing rows. Output is capped at `diff_limit` (default 50) via `_expand`,
with `diff_truncated` flagging overflow; each divergent row is split back into
cells for readability.

## 1.6 The other half: dialect normalization in the SQL (`queries.py`)

Python canonicalization handles *value representation*; some differences must be
neutralized **inside the database**. That is why every query is a **pair**
(`postgres_sql` / `mysql_sql`) producing logically identical output:

- **Date formatting** (Q08): `TO_CHAR(order_ts,'YYYY-MM')` vs `DATE_FORMAT(order_ts,'%Y-%m')`
- **Year extraction** (Q17): `EXTRACT(YEAR FROM ...)` vs `YEAR(...)`
- **String concat** (Q20): `country || '-' || segment` vs `CONCAT(...)`
- **Aggregates** wrapped in `ROUND(...,2/4)` + `CAST` so both engines emit
  comparable numeric types/precision *before* `_canon_number` runs.

**Belt and suspenders:** the SQL pairs make outputs *logically* identical; Python
canonicalization absorbs the *representational* residue.

## 1.7 Design trade-offs

| Choice | Reason |
|---|---|
| Canonical **strings**, not typed compare | one uniform path for every SQL type; sortable; hashable |
| Round to **6 dp** | collapse engine precision noise without hiding real diffs |
| **Sort + hash** | order-independence + cheap, storable equality proxy |
| **Multiset** `Counter` diff | detect duplicate-count drift; actionable output |
| **Diff cap** (`diff_limit`) | a wildly divergent query can't produce a multi-GB file |
| **Exit 0 on "inconsistent"** | inconsistency is a business *finding*, not an infra failure (`compare.py:11`); only genuine errors exit non-zero so Step Functions retries |

## 1.8 Scaling caveat

The logic is **correct but memory-bound**: `fetchall()` + `a_canon`/`b_canon` +
two `sorted()` copies hold ~3–4× the result set in RAM for *both* engines at
once. Fine at 50K rows; **OOMs at millions**. The fix: push
canonicalize→sort→hash **into SQL** (`ORA_HASH`/`HASH` over identically-rounded
values) so only the fingerprint crosses the wire — the same order-independent
multiset semantics, relocated into the database.

---

# Part 2 — Output Deep Dive

A run produces **six** distinct outputs. Because the pipeline writes everything
to S3, the Step Functions execution output itself is intentionally thin.

```
Execution output (API)  →  run_id + pointer to manifest      (thin: just locations)
        │
        ▼ s3://<bucket>/runs/<run_id>/
        ├── queries.json             ← prep: the work list (Map input)
        ├── results/<qid>.json × N   ← compare: per-query verdicts (raw truth)
        ├── map-output/manifest.json ← Step Functions: which iterations ran
        └── report.json              ← aggregate: the rolled-up answer  ◄── READ THIS
                                          └─ also pushed via SNS
```

## 2.1 The Step Functions execution output

`Aggregate` has `ResultPath: null` (discards its ECS output), so the final
execution output is the data that *entered* Aggregate:

```json
{
  "run_id": "a1b2c3d4-5678-90ab-cdef-1234567890ab",
  "map_results": {
    "MapRunArn": "arn:aws:states:us-east-1:123456789012:mapRun:query-matcher-consistency/a1b2...:e9f8...",
    "ResultWriterDetails": {
      "Bucket": "query-matcher-...",
      "Key": "runs/a1b2.../map-output/manifest.json"
    }
  }
}
```

> **Surprise:** because the Map has a `ResultWriter`, `map_results` is a
> **pointer**, not the array of verdicts. With 10,000 queries the verdicts would
> blow the 256 KB state-data limit, so Distributed Map offloads them to S3. The
> execution output says *"here's the run_id and where the manifest is"* — the
> data itself lives in S3.

## 2.2 `runs/<run_id>/queries.json` — prep output / Map input

A JSON **array**, one object per query (written by `prep.run()`):

```json
[
  {
    "query_id": "Q01_total_orders",
    "description": "Total number of orders",
    "category": "aggregate/count",
    "postgres_sql": "SELECT COUNT(*) AS order_count FROM orders",
    "mysql_sql": "SELECT COUNT(*) AS order_count FROM orders"
  },
  { "query_id": "Q02_distinct_customers", "...": "..." }
]
```

## 2.3 `runs/<run_id>/results/<query_id>.json` — per-query verdict

Written by `compare.run()`. **Three shapes** by outcome.

### (a) Consistent — engines agree

```json
{
  "query_id": "Q01_total_orders",
  "run_id": "a1b2c3d4-...",
  "description": "Total number of orders",
  "category": "aggregate/count",
  "status": "consistent",
  "postgres_columns": ["order_count"],
  "mysql_columns": ["order_count"],
  "postgres_ms": 12.4,
  "mysql_ms": 9.8,
  "consistent": true,
  "postgres_row_count": 1,
  "mysql_row_count": 1,
  "postgres_hash": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
  "mysql_hash":    "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
}
```
The two `*_hash` values are **identical** — that *is* `consistent: true`.

### (b) Inconsistent — adds a `diff` block

```json
{
  "query_id": "Q04_revenue_by_category",
  "run_id": "a1b2c3d4-...",
  "status": "inconsistent",
  "consistent": false,
  "postgres_row_count": 8,
  "mysql_row_count": 8,
  "postgres_hash": "aaa...",
  "mysql_hash":    "bbb...",
  "postgres_columns": ["category", "revenue"],
  "mysql_columns": ["category", "revenue"],
  "postgres_ms": 210.5,
  "mysql_ms": 198.1,
  "diff": {
    "row_count_match": true,
    "only_in_postgres": [["Electronics", "152340.55"]],
    "only_in_mysql":    [["Electronics", "152340.50"]],
    "only_in_postgres_total": 1,
    "only_in_mysql_total": 1,
    "diff_truncated": false
  }
}
```
Here: one category's revenue differs by 5 cents. `diff` is capped at
`diff_limit` (50).

### (c) Error — query couldn't run

Written **before** the task re-raises, so it survives a failed iteration:

```json
{
  "query_id": "Q07_avg_amount_by_segment",
  "run_id": "a1b2c3d4-...",
  "description": "...",
  "category": "...",
  "status": "error",
  "consistent": false,
  "error": "(psycopg2.OperationalError) connection timed out"
}
```

## 2.4 `runs/<run_id>/map-output/manifest.json` — Map manifest

Written by the Distributed Map's `ResultWriter` (managed by Step Functions, not
app code). Catalogs each iteration and points to its output file. `aggregate`
ignores it and reads `results/*.json` directly.

```json
{
  "DestinationBucket": "query-matcher-...",
  "MapRunArn": "arn:aws:states:...:mapRun:...",
  "ResultFiles": {
    "SUCCEEDED": [{ "Key": "runs/a1b2.../map-output/SUCCEEDED_0.json", "Size": 1234 }],
    "FAILED": [],
    "PENDING": []
  }
}
```

## 2.5 `runs/<run_id>/report.json` — the headline output

Written by `aggregate.run()` — the document a human reads:

```json
{
  "run_id": "a1b2c3d4-...",
  "totals": { "queries": 20, "consistent": 18, "inconsistent": 1, "errored": 1 },
  "overall": "FAIL",
  "inconsistent_query_ids": ["Q04_revenue_by_category"],
  "errored_query_ids": ["Q07_avg_amount_by_segment"],
  "results": [
    { "query_id": "Q01_total_orders", "status": "consistent", "...": "..." },
    { "query_id": "Q02_distinct_customers", "status": "consistent", "...": "..." }
  ]
}
```

- `totals` — tally across all four status buckets.
- `overall` — **`"PASS"` only if `inconsistent` and `errored` are both empty**;
  else `"FAIL"` (`aggregate.py:41`).
- `inconsistent_query_ids` / `errored_query_ids` — sorted quick-scan lists.
- `results` — **all** per-query verdicts (every §2.3 file) inlined, sorted by
  `query_id`.

## 2.6 The SNS notification

`aggregate._notify()` publishes a plain-text summary; the verdict is in the
subject:

```
Subject: [FAIL] Query consistency run a1b2c3d4-... (18/20 consistent)

Run ID        : a1b2c3d4-...
Overall       : FAIL
Queries       : 20
Consistent    : 18
Inconsistent  : 1
Errored       : 1
Report        : s3://query-matcher-.../runs/a1b2.../report.json
Inconsistent  : Q04_revenue_by_category
Errored       : Q07_avg_amount_by_segment
```

## 2.7 Where to look for what

| Question | Artifact |
|---|---|
| Did the whole run pass? | `report.json` → `overall`; SNS subject |
| Which queries diverged? | `report.json` → `inconsistent_query_ids` / `errored_query_ids` |
| *How* did a query diverge (which rows)? | `results/<qid>.json` → `diff` |
| Per-engine timing / row counts / hashes | `results/<qid>.json` |
| Which iterations ran / failed at the infra level | `map-output/manifest.json` |
| What SQL was executed | `queries.json` |

> **Key takeaway:** the execution's own output is thin (run_id + an S3 pointer)
> because **S3 is the result store, not Step Functions state**. The output you
> care about is **`runs/<run_id>/report.json`** (mirrored by SNS); per-query
> detail — diffs, timings, hashes — sits beside it in `results/*.json`. The
> `overall: PASS/FAIL` in that report, *not* the execution's success status, is
> the business verdict.

---

## One-paragraph summary

The app proves two engines agree by reducing each result set to an
**order-independent fingerprint**: it canonicalizes every cell to a stable string
(taming NULL/bool/date/precision differences), sorts the rows to erase ordering,
and SHA-256-hashes them — declaring **consistency when hashes and row counts
match**; on mismatch it runs a multiset `Counter` diff to surface exactly which
rows diverge, while dialect differences are pre-normalized in the paired SQL so
any detected difference is real. The results are written to S3 as per-query
verdicts and rolled up into `report.json` (PASS/FAIL + totals + diffs), with the
Step Functions execution returning only a thin pointer to that S3 location.
