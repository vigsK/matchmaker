"""
Database connectivity for both engines via SQLAlchemy.

We emulate "an RDBMS connection" for two distinct relational systems:
  * PostgreSQL  (psycopg2 driver)  - the "Oracle side"
  * MySQL       (PyMySQL driver)   - the "Snowflake side"

Connections use short connect-timeouts and a single pooled engine per process
so the per-query Fargate task starts fast and fails fast.
"""
from __future__ import annotations

import time
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine

from .config import DbCreds

_CONNECT_TIMEOUT = 15

_DRIVERS = {"postgres": "postgresql+psycopg2", "mysql": "mysql+pymysql"}


def _url(creds: DbCreds) -> URL:
    drivername = _DRIVERS.get(creds.engine)
    if drivername is None:
        raise ValueError(f"unsupported engine: {creds.engine}")
    # URL.create() percent-escapes credentials, so any special characters in the
    # generated password are handled safely.
    return URL.create(
        drivername=drivername,
        username=creds.username,
        password=creds.password,
        host=creds.host,
        port=creds.port,
        database=creds.dbname,
    )


def _connect_args(creds: DbCreds) -> dict:
    if creds.engine == "postgres":
        return {"connect_timeout": _CONNECT_TIMEOUT}
    if creds.engine == "mysql":
        return {"connect_timeout": _CONNECT_TIMEOUT, "read_timeout": 600,
                "write_timeout": 600}
    return {}


def make_engine(creds: DbCreds) -> Engine:
    return create_engine(
        _url(creds),
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args=_connect_args(creds),
        future=True,
    )


def wait_for_db(engine: Engine, attempts: int = 30, delay: float = 10.0) -> None:
    """Block until the engine accepts a connection (used by the seed task while
    Aurora Serverless v2 is still warming up)."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except Exception as exc:  # noqa: BLE001 - we genuinely retry on anything
            last = exc
            print(f"[db] not ready (attempt {i + 1}/{attempts}): {exc}")
            time.sleep(delay)
    raise RuntimeError(f"database never became ready: {last}")


def run_query(engine: Engine, sql: str) -> tuple[list[str], list[tuple[Any, ...]], float]:
    """Execute a read query; return (columns, rows, elapsed_seconds)."""
    started = time.perf_counter()
    with engine.connect() as conn:
        result = conn.execute(text(sql))
        columns = list(result.keys())
        rows = [tuple(r) for r in result.fetchall()]
    elapsed = time.perf_counter() - started
    return columns, rows, elapsed
