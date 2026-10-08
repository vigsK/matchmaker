"""
Runtime configuration.

The same container image runs in four MODEs (prep | compare | aggregate | seed),
driven entirely by environment variables injected by Step Functions / ECS.  No
secrets are baked into the image: DB credentials are read at runtime from AWS
Secrets Manager using the task IAM role.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache

import boto3


def env(name: str, default: str | None = None, required: bool = False) -> str:
    val = os.environ.get(name, default)
    if required and (val is None or val == ""):
        raise RuntimeError(f"required environment variable {name} is not set")
    return val  # type: ignore[return-value]


@dataclass(frozen=True)
class DbCreds:
    engine: str          # "postgres" | "mysql"
    host: str
    port: int
    username: str
    password: str
    dbname: str


@dataclass(frozen=True)
class Config:
    mode: str
    region: str
    bucket: str
    run_id: str
    query_id: str
    excel_key: str
    pg_secret_arn: str
    mysql_secret_arn: str
    sns_topic_arn: str
    row_count: int
    diff_limit: int

    @staticmethod
    def from_env() -> "Config":
        return Config(
            mode=env("MODE", required=True),
            region=env("AWS_REGION", env("AWS_DEFAULT_REGION", "us-east-1")),
            bucket=env("S3_BUCKET", required=True),
            run_id=env("RUN_ID", "manual"),
            query_id=env("QUERY_ID", ""),
            excel_key=env("EXCEL_KEY", "input/queries.xlsx"),
            pg_secret_arn=env("PG_SECRET_ARN", ""),
            mysql_secret_arn=env("MYSQL_SECRET_ARN", ""),
            sns_topic_arn=env("SNS_TOPIC_ARN", ""),
            row_count=int(env("ROW_COUNT", "50000")),
            diff_limit=int(env("RESULT_DIFF_LIMIT", "50")),
        )


@lru_cache(maxsize=8)
def get_creds(secret_arn: str, region: str) -> DbCreds:
    """Fetch and parse a DB credential secret.  Cached per-process."""
    client = boto3.client("secretsmanager", region_name=region)
    raw = client.get_secret_value(SecretId=secret_arn)["SecretString"]
    data = json.loads(raw)
    return DbCreds(
        engine=data["engine"],
        host=data["host"],
        port=int(data["port"]),
        username=data["username"],
        password=data["password"],
        dbname=data["dbname"],
    )
