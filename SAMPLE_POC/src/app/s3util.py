"""Thin S3 helpers shared by every mode."""
from __future__ import annotations

import gzip
import io
import json
from typing import Any

import boto3

_s3 = None


def s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def put_json(bucket: str, key: str, obj: Any) -> None:
    body = json.dumps(obj, default=str, ensure_ascii=False).encode("utf-8")
    s3().put_object(Bucket=bucket, Key=key, Body=body,
                    ContentType="application/json")


def get_json(bucket: str, key: str) -> Any:
    resp = s3().get_object(Bucket=bucket, Key=key)
    return json.loads(resp["Body"].read())


def get_bytes(bucket: str, key: str) -> bytes:
    resp = s3().get_object(Bucket=bucket, Key=key)
    return resp["Body"].read()


def list_keys(bucket: str, prefix: str) -> list[str]:
    keys: list[str] = []
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        resp = s3().list_objects_v2(**kwargs)
        for item in resp.get("Contents", []):
            keys.append(item["Key"])
        if resp.get("IsTruncated"):
            token = resp.get("NextContinuationToken")
        else:
            break
    return keys


def maybe_gunzip(data: bytes) -> bytes:
    """Step Functions ResultWriter manifests are plain JSON, but child result
    artifacts may be gzipped depending on size; transparently handle both."""
    if data[:2] == b"\x1f\x8b":
        return gzip.decompress(data)
    return data
