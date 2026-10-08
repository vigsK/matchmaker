"""
MODE=seed  -  one-time bootstrap.

Generates a single deterministic synthetic dataset (seeded RNG) and loads the
*identical* rows into BOTH Aurora clusters.  Because the data is generated once
in Python and inserted verbatim into each engine, the two databases are
guaranteed to start byte-for-byte equivalent - which is the whole premise of the
consistency check.

Volumes (defaults, override with ROW_COUNT for the orders table):
    customers : 2,000   (200 of them intentionally have no orders)
    products  :   200
    orders    : ROW_COUNT (default 50,000)
"""
from __future__ import annotations

from datetime import datetime, timedelta
from random import Random

from sqlalchemy.engine import Engine

from .config import Config, get_creds
from .db import make_engine, wait_for_db

N_CUSTOMERS = 2000
N_NO_ORDER_CUSTOMERS = 200          # customers deliberately left without orders
N_PRODUCTS = 200
BATCH = 5000

COUNTRIES = ["US", "GB", "DE", "FR", "IN", "JP", "BR", "CA", "AU", "SG"]
SEGMENTS = ["Consumer", "SMB", "Enterprise", "Government"]
CATEGORIES = ["Electronics", "Apparel", "Home", "Toys", "Grocery",
              "Sports", "Beauty", "Books"]
STATUSES = ["NEW", "PAID", "SHIPPED", "DELIVERED", "CANCELLED"]
FIRST = ["Aaron", "Alice", "Amir", "Anna", "Ben", "Carla", "David", "Elena",
         "Frank", "Grace", "Hiro", "Ivan", "Julia", "Karan", "Lena", "Mohan",
         "Nora", "Omar", "Priya", "Quinn", "Ravi", "Sara", "Tom", "Uma",
         "Victor", "Wendy", "Xena", "Yuki", "Zach", "Aditi"]
LAST = ["Smith", "Khan", "Garcia", "Müller", "Sato", "Silva", "Brown", "Patel",
        "Nguyen", "Rossi", "Kim", "Singh", "Lopez", "Dubois", "Chen"]

DDL = {
    "postgres": [
        "DROP TABLE IF EXISTS orders",
        "DROP TABLE IF EXISTS products",
        "DROP TABLE IF EXISTS customers",
        """CREATE TABLE customers (
              customer_id INTEGER PRIMARY KEY,
              full_name   VARCHAR(120),
              country     VARCHAR(2),
              segment     VARCHAR(20),
              signup_date DATE)""",
        """CREATE TABLE products (
              product_id   INTEGER PRIMARY KEY,
              product_name VARCHAR(120),
              category     VARCHAR(40),
              unit_price   NUMERIC(10,2))""",
        """CREATE TABLE orders (
              order_id     INTEGER PRIMARY KEY,
              customer_id  INTEGER,
              product_id   INTEGER,
              quantity     INTEGER,
              order_status VARCHAR(20),
              order_ts     TIMESTAMP,
              amount       NUMERIC(12,2))""",
        "CREATE INDEX idx_orders_customer ON orders(customer_id)",
        "CREATE INDEX idx_orders_product ON orders(product_id)",
        "CREATE INDEX idx_orders_ts ON orders(order_ts)",
    ],
    "mysql": [
        "DROP TABLE IF EXISTS orders",
        "DROP TABLE IF EXISTS products",
        "DROP TABLE IF EXISTS customers",
        """CREATE TABLE customers (
              customer_id INT PRIMARY KEY,
              full_name   VARCHAR(120),
              country     VARCHAR(2),
              segment     VARCHAR(20),
              signup_date DATE)""",
        """CREATE TABLE products (
              product_id   INT PRIMARY KEY,
              product_name VARCHAR(120),
              category     VARCHAR(40),
              unit_price   DECIMAL(10,2))""",
        """CREATE TABLE orders (
              order_id     INT PRIMARY KEY,
              customer_id  INT,
              product_id   INT,
              quantity     INT,
              order_status VARCHAR(20),
              order_ts     DATETIME,
              amount       DECIMAL(12,2),
              INDEX idx_orders_customer (customer_id),
              INDEX idx_orders_product (product_id),
              INDEX idx_orders_ts (order_ts))""",
    ],
}

INSERTS = {
    "customers": "INSERT INTO customers (customer_id, full_name, country, segment, signup_date) "
                 "VALUES (%s, %s, %s, %s, %s)",
    "products": "INSERT INTO products (product_id, product_name, category, unit_price) "
                "VALUES (%s, %s, %s, %s)",
    "orders": "INSERT INTO orders (order_id, customer_id, product_id, quantity, order_status, "
              "order_ts, amount) VALUES (%s, %s, %s, %s, %s, %s, %s)",
}


def _generate(row_count: int):
    """Deterministically build identical customers/products/orders datasets."""
    rng = Random(42)
    base_ts = datetime(2023, 1, 1, 0, 0, 0)
    span_seconds = int((datetime(2025, 1, 1) - base_ts).total_seconds())

    customers = []
    for cid in range(1, N_CUSTOMERS + 1):
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        signup = (base_ts - timedelta(days=rng.randint(0, 1200))).date()
        customers.append((cid, name, rng.choice(COUNTRIES),
                          rng.choice(SEGMENTS), signup))

    products = []
    for pid in range(1, N_PRODUCTS + 1):
        price = round(rng.uniform(5.0, 500.0), 2)
        products.append((pid, f"Product-{pid:04d}", rng.choice(CATEGORIES), price))
    price_by_pid = {p[0]: p[3] for p in products}

    max_ordering_customer = N_CUSTOMERS - N_NO_ORDER_CUSTOMERS
    orders = []
    for oid in range(1, row_count + 1):
        cid = rng.randint(1, max_ordering_customer)
        pid = rng.randint(1, N_PRODUCTS)
        qty = rng.randint(1, 10)
        status = rng.choice(STATUSES)
        ts = base_ts + timedelta(seconds=rng.randint(0, span_seconds - 1))
        amount = round(qty * price_by_pid[pid], 2)
        orders.append((oid, cid, pid, qty, status, ts, amount))

    return customers, products, orders


def _bulk_insert(engine: Engine, is_postgres: bool, sql: str, rows: list) -> None:
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        if is_postgres:
            from psycopg2.extras import execute_batch
            for i in range(0, len(rows), BATCH):
                execute_batch(cur, sql, rows[i:i + BATCH], page_size=BATCH)
                raw.commit()
        else:
            for i in range(0, len(rows), BATCH):
                cur.executemany(sql, rows[i:i + BATCH])
                raw.commit()
        cur.close()
    finally:
        raw.close()


def _load(engine: Engine, engine_name: str, customers, products, orders) -> None:
    is_pg = engine_name == "postgres"
    print(f"[seed] ({engine_name}) creating schema")
    with engine.begin() as conn:
        from sqlalchemy import text
        for stmt in DDL[engine_name]:
            conn.execute(text(stmt))
    print(f"[seed] ({engine_name}) loading {len(customers)} customers, "
          f"{len(products)} products, {len(orders)} orders")
    _bulk_insert(engine, is_pg, INSERTS["customers"], customers)
    _bulk_insert(engine, is_pg, INSERTS["products"], products)
    _bulk_insert(engine, is_pg, INSERTS["orders"], orders)
    print(f"[seed] ({engine_name}) done")


def run(cfg: Config) -> dict:
    print(f"[seed] generating deterministic dataset (row_count={cfg.row_count})")
    customers, products, orders = _generate(cfg.row_count)

    pg = make_engine(get_creds(cfg.pg_secret_arn, cfg.region))
    my = make_engine(get_creds(cfg.mysql_secret_arn, cfg.region))

    print("[seed] waiting for both databases to accept connections ...")
    wait_for_db(pg)
    wait_for_db(my)

    _load(pg, "postgres", customers, products, orders)
    _load(my, "mysql", customers, products, orders)

    summary = {
        "customers": len(customers),
        "products": len(products),
        "orders": len(orders),
        "customers_without_orders": N_NO_ORDER_CUSTOMERS,
    }
    print(f"[seed] complete: {summary}")
    return summary
