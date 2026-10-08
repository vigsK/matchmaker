"""
MODE=aggregate  -  final state of the workflow.

Collects every per-query verdict under runs/<run_id>/results/, builds a summary
report, writes it to runs/<run_id>/report.json, and publishes an SNS
notification.  The Step Functions execution succeeds regardless of consistency;
the *report* carries the business verdict.
"""
from __future__ import annotations

import boto3

from .config import Config
from .s3util import get_json, list_keys, put_json


def run(cfg: Config) -> dict:
    prefix = f"runs/{cfg.run_id}/results/"
    keys = [k for k in list_keys(cfg.bucket, prefix) if k.endswith(".json")]
    print(f"[aggregate] run_id={cfg.run_id} found {len(keys)} result files")

    results = []
    for k in keys:
        try:
            results.append(get_json(cfg.bucket, k))
        except Exception as exc:  # noqa: BLE001
            print(f"[aggregate] could not read {k}: {exc}")

    consistent = [r for r in results if r.get("status") == "consistent"]
    inconsistent = [r for r in results if r.get("status") == "inconsistent"]
    errored = [r for r in results if r.get("status") == "error"]

    report = {
        "run_id": cfg.run_id,
        "totals": {
            "queries": len(results),
            "consistent": len(consistent),
            "inconsistent": len(inconsistent),
            "errored": len(errored),
        },
        "overall": "PASS" if (inconsistent or errored) == [] else "FAIL",
        "inconsistent_query_ids": sorted(r["query_id"] for r in inconsistent),
        "errored_query_ids": sorted(r["query_id"] for r in errored),
        "results": sorted(results, key=lambda r: r.get("query_id", "")),
    }

    report_key = f"runs/{cfg.run_id}/report.json"
    put_json(cfg.bucket, report_key, report)
    print(
        f"[aggregate] overall={report['overall']} "
        f"consistent={len(consistent)} inconsistent={len(inconsistent)} "
        f"errored={len(errored)} -> s3://{cfg.bucket}/{report_key}"
    )

    if cfg.sns_topic_arn:
        _notify(cfg, report, report_key)

    return report


def _notify(cfg: Config, report: dict, report_key: str) -> None:
    t = report["totals"]
    subject = (
        f"[{report['overall']}] Query consistency run {cfg.run_id} "
        f"({t['consistent']}/{t['queries']} consistent)"
    )[:100]
    lines = [
        f"Run ID        : {cfg.run_id}",
        f"Overall       : {report['overall']}",
        f"Queries       : {t['queries']}",
        f"Consistent    : {t['consistent']}",
        f"Inconsistent  : {t['inconsistent']}",
        f"Errored       : {t['errored']}",
        f"Report        : s3://{cfg.bucket}/{report_key}",
    ]
    if report["inconsistent_query_ids"]:
        lines.append("Inconsistent  : " + ", ".join(report["inconsistent_query_ids"]))
    if report["errored_query_ids"]:
        lines.append("Errored       : " + ", ".join(report["errored_query_ids"]))
    try:
        boto3.client("sns", region_name=cfg.region).publish(
            TopicArn=cfg.sns_topic_arn, Subject=subject, Message="\n".join(lines)
        )
        print("[aggregate] SNS notification published")
    except Exception as exc:  # noqa: BLE001
        print(f"[aggregate] SNS publish failed (non-fatal): {exc}")
