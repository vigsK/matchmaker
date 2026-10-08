"""
Order-independent result-set comparison.

Two query result sets are "consistent" when, after normalising every cell to a
canonical string, they contain the *same multiset of rows* (row order ignored)
and the *same number of columns per row*.

On mismatch we surface:
  * row-count delta,
  * a multiset diff (rows present on one side but not the other), capped at
    ``diff_limit`` rows per side, so an operator can immediately see WHERE the
    two engines diverge.

A SHA-256 over the sorted, canonicalised rows gives a cheap fingerprint that is
stored with every result for quick equality checks and audit.
"""
from __future__ import annotations

import datetime
import hashlib
from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Sequence

NULL_TOKEN = "∅"          # ∅  - unambiguous stand-in for SQL NULL
CELL_SEP = ""            # ASCII unit separator between cells
ROW_SEP = ""            # ASCII record separator between rows


def _canon_number(value: Any) -> str:
    """Canonicalise any numeric to a stable decimal string.

    Rounds to 6 dp (collapsing engine precision differences), drops trailing
    zeros, and never uses scientific notation so 1E+2 and 100 compare equal.
    """
    try:
        dec = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value).strip()
    dec = dec.quantize(Decimal("1.000000"))           # 6 dp
    text = format(dec, "f")                            # plain decimal, no sci-notation
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def canon_cell(value: Any) -> str:
    """Normalise a single DB cell to a canonical, engine-agnostic string."""
    if value is None:
        return NULL_TOKEN
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (Decimal, float)):
        return _canon_number(value)
    if isinstance(value, datetime.datetime):
        return value.replace(microsecond=0).isoformat(sep=" ")
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, datetime.timedelta):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return str(value).strip()


def canon_row(row: Sequence[Any]) -> str:
    return CELL_SEP.join(canon_cell(c) for c in row)


def _fingerprint(canon_rows_sorted: Iterable[str]) -> str:
    h = hashlib.sha256()
    for r in canon_rows_sorted:
        h.update(r.encode("utf-8"))
        h.update(ROW_SEP.encode("utf-8"))
    return h.hexdigest()


def compare_result_sets(
    a_rows: Sequence[Sequence[Any]],
    b_rows: Sequence[Sequence[Any]],
    a_label: str = "postgres",
    b_label: str = "mysql",
    diff_limit: int = 50,
) -> dict:
    """Compare two result sets order-independently.

    Returns a JSON-serialisable dict describing the verdict.
    """
    a_canon = [canon_row(r) for r in a_rows]
    b_canon = [canon_row(r) for r in b_rows]

    a_sorted = sorted(a_canon)
    b_sorted = sorted(b_canon)

    a_hash = _fingerprint(a_sorted)
    b_hash = _fingerprint(b_sorted)

    a_count = len(a_canon)
    b_count = len(b_canon)

    consistent = (a_hash == b_hash) and (a_count == b_count)

    result: dict[str, Any] = {
        "consistent": consistent,
        f"{a_label}_row_count": a_count,
        f"{b_label}_row_count": b_count,
        f"{a_label}_hash": a_hash,
        f"{b_label}_hash": b_hash,
    }

    if not consistent:
        a_counter = Counter(a_canon)
        b_counter = Counter(b_canon)
        only_a = a_counter - b_counter          # rows on A side, missing/short on B
        only_b = b_counter - a_counter          # rows on B side, missing/short on A

        def _expand(counter: Counter) -> list[list[str]]:
            out: list[list[str]] = []
            for row, n in counter.items():
                for _ in range(n):
                    if len(out) >= diff_limit:
                        return out
                    out.append(row.split(CELL_SEP))
            return out

        result["diff"] = {
            "row_count_match": a_count == b_count,
            f"only_in_{a_label}": _expand(only_a),
            f"only_in_{b_label}": _expand(only_b),
            f"only_in_{a_label}_total": sum(only_a.values()),
            f"only_in_{b_label}_total": sum(only_b.values()),
            "diff_truncated": (
                sum(only_a.values()) > diff_limit or sum(only_b.values()) > diff_limit
            ),
        }

    return result
