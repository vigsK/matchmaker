"""
Container entrypoint.  A single image, four MODEs, selected by the MODE env var:

    MODE=seed       bootstrap both Aurora clusters with identical data
    MODE=prep       excel -> dataframe -> queries.json in S3   (SFN state 1)
    MODE=compare    run one query pair on both engines + compare (Distributed Map)
    MODE=aggregate  collect verdicts -> report.json + SNS       (SFN final state)

Usage (inside container):  python -m app.main <mode?>
The mode may also be passed as argv[1] (overrides MODE env), which makes local
testing and `docker run image seed` ergonomic.
"""
from __future__ import annotations

import json
import os
import sys

from .config import Config


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1]:
        os.environ["MODE"] = sys.argv[1]

    cfg = Config.from_env()
    print(f"=== query-matcher mode={cfg.mode} run_id={cfg.run_id} "
          f"query_id={cfg.query_id or '-'} ===")

    if cfg.mode == "seed":
        from . import seed
        out = seed.run(cfg)
    elif cfg.mode == "prep":
        from . import prep
        out = prep.run(cfg)
    elif cfg.mode == "compare":
        from . import compare
        out = compare.run(cfg)
    elif cfg.mode == "aggregate":
        from . import aggregate
        out = aggregate.run(cfg)
    else:
        raise SystemExit(f"unknown MODE={cfg.mode!r} (expected seed|prep|compare|aggregate)")

    print("=== RESULT ===")
    print(json.dumps(out, default=str, indent=2)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
