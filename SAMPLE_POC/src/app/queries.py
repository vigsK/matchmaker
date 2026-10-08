"""
Single source of truth for:
  * the relational schema seeded into BOTH database engines, and
  * the 20 logically-equivalent query pairs (PostgreSQL dialect / MySQL dialect)
    that the workflow runs on both engines to prove result consistency.

The scenario is "Oracle vs Snowflake". For a reproducible, license-free,
fully-serverless POC this is emulated with two Amazon Aurora Serverless v2
clusters:  engine_a = PostgreSQL  (stands in for the Oracle side)
           engine_b = MySQL       (stands in for the Snowflake side)

Every query pair is written so that, given identical seed data, both engines
MUST return the same *set* of rows.  Dialect differences (string concat, date
formatting, year extraction, ...) are handled per-engine but normalised to the
same logical output.  Aggregates are wrapped in ROUND(...,2) / explicit CASTs so
the two engines emit comparable numeric precision.
"""

# --------------------------------------------------------------------------- #
# Schema (logical).  DDL is rendered per-dialect in seed.py.                   #
# --------------------------------------------------------------------------- #
TABLES = ["customers", "products", "orders"]

# --------------------------------------------------------------------------- #
# 20 query pairs.  Each item:                                                  #
#   query_id    - stable identifier (also the S3 result key)                   #
#   description - human readable                                               #
#   category    - kind of SQL exercised                                        #
#   postgres_sql / mysql_sql - dialect specific, logically identical           #
# --------------------------------------------------------------------------- #
QUERY_PAIRS = [
    {
        "query_id": "Q01_total_orders",
        "description": "Total number of orders",
        "category": "aggregate/count",
        "postgres_sql": "SELECT COUNT(*) AS order_count FROM orders",
        "mysql_sql":    "SELECT COUNT(*) AS order_count FROM orders",
    },
    {
        "query_id": "Q02_distinct_customers",
        "description": "Distinct customers that placed at least one order",
        "category": "aggregate/distinct",
        "postgres_sql": "SELECT COUNT(DISTINCT customer_id) AS c FROM orders",
        "mysql_sql":    "SELECT COUNT(DISTINCT customer_id) AS c FROM orders",
    },
    {
        "query_id": "Q03_total_revenue",
        "description": "Total revenue across all orders",
        "category": "aggregate/sum",
        "postgres_sql": "SELECT ROUND(SUM(amount), 2) AS revenue FROM orders",
        "mysql_sql":    "SELECT ROUND(SUM(amount), 2) AS revenue FROM orders",
    },
    {
        "query_id": "Q04_revenue_by_category",
        "description": "Revenue grouped by product category (join + group by)",
        "category": "join/group-by",
        "postgres_sql": (
            "SELECT p.category AS category, ROUND(SUM(o.amount), 2) AS revenue "
            "FROM orders o JOIN products p ON o.product_id = p.product_id "
            "GROUP BY p.category ORDER BY p.category"
        ),
        "mysql_sql": (
            "SELECT p.category AS category, ROUND(SUM(o.amount), 2) AS revenue "
            "FROM orders o JOIN products p ON o.product_id = p.product_id "
            "GROUP BY p.category ORDER BY p.category"
        ),
    },
    {
        "query_id": "Q05_orders_by_status",
        "description": "Order count grouped by status",
        "category": "group-by",
        "postgres_sql": (
            "SELECT order_status, COUNT(*) AS n FROM orders "
            "GROUP BY order_status ORDER BY order_status"
        ),
        "mysql_sql": (
            "SELECT order_status, COUNT(*) AS n FROM orders "
            "GROUP BY order_status ORDER BY order_status"
        ),
    },
    {
        "query_id": "Q06_top10_customers_by_spend",
        "description": "Top 10 customers by total spend (deterministic tiebreak)",
        "category": "join/group-by/limit",
        "postgres_sql": (
            "SELECT c.customer_id, ROUND(SUM(o.amount), 2) AS spend "
            "FROM orders o JOIN customers c ON o.customer_id = c.customer_id "
            "GROUP BY c.customer_id "
            "ORDER BY spend DESC, c.customer_id ASC LIMIT 10"
        ),
        "mysql_sql": (
            "SELECT c.customer_id, ROUND(SUM(o.amount), 2) AS spend "
            "FROM orders o JOIN customers c ON o.customer_id = c.customer_id "
            "GROUP BY c.customer_id "
            "ORDER BY spend DESC, c.customer_id ASC LIMIT 10"
        ),
    },
    {
        "query_id": "Q07_avg_amount_by_segment",
        "description": "Average order amount by customer segment",
        "category": "join/group-by/avg",
        "postgres_sql": (
            "SELECT c.segment, ROUND(AVG(o.amount), 2) AS avg_amount "
            "FROM orders o JOIN customers c ON o.customer_id = c.customer_id "
            "GROUP BY c.segment ORDER BY c.segment"
        ),
        "mysql_sql": (
            "SELECT c.segment, ROUND(AVG(o.amount), 2) AS avg_amount "
            "FROM orders o JOIN customers c ON o.customer_id = c.customer_id "
            "GROUP BY c.segment ORDER BY c.segment"
        ),
    },
    {
        "query_id": "Q08_monthly_revenue",
        "description": "Revenue per calendar month (date formatting dialects)",
        "category": "date-func/group-by",
        "postgres_sql": (
            "SELECT TO_CHAR(order_ts, 'YYYY-MM') AS ym, ROUND(SUM(amount), 2) AS revenue "
            "FROM orders GROUP BY TO_CHAR(order_ts, 'YYYY-MM') ORDER BY ym"
        ),
        "mysql_sql": (
            "SELECT DATE_FORMAT(order_ts, '%Y-%m') AS ym, ROUND(SUM(amount), 2) AS revenue "
            "FROM orders GROUP BY DATE_FORMAT(order_ts, '%Y-%m') ORDER BY ym"
        ),
    },
    {
        "query_id": "Q09_orders_by_country",
        "description": "Order count per customer country",
        "category": "join/group-by",
        "postgres_sql": (
            "SELECT c.country, COUNT(*) AS n "
            "FROM orders o JOIN customers c ON o.customer_id = c.customer_id "
            "GROUP BY c.country ORDER BY c.country"
        ),
        "mysql_sql": (
            "SELECT c.country, COUNT(*) AS n "
            "FROM orders o JOIN customers c ON o.customer_id = c.customer_id "
            "GROUP BY c.country ORDER BY c.country"
        ),
    },
    {
        "query_id": "Q10_customers_without_orders",
        "description": "Count of customers that never ordered (anti-join)",
        "category": "left-join/null",
        "postgres_sql": (
            "SELECT COUNT(*) AS c FROM customers c "
            "LEFT JOIN orders o ON o.customer_id = c.customer_id "
            "WHERE o.order_id IS NULL"
        ),
        "mysql_sql": (
            "SELECT COUNT(*) AS c FROM customers c "
            "LEFT JOIN orders o ON o.customer_id = c.customer_id "
            "WHERE o.order_id IS NULL"
        ),
    },
    {
        "query_id": "Q11_max_order_amount",
        "description": "Largest single order amount",
        "category": "aggregate/max",
        "postgres_sql": "SELECT ROUND(MAX(amount), 2) AS max_amount FROM orders",
        "mysql_sql":    "SELECT ROUND(MAX(amount), 2) AS max_amount FROM orders",
    },
    {
        "query_id": "Q12_stats_by_category",
        "description": "min/avg/max amount per product category",
        "category": "join/group-by/stats",
        "postgres_sql": (
            "SELECT p.category, ROUND(MIN(o.amount),2) AS mn, ROUND(AVG(o.amount),2) AS av, "
            "ROUND(MAX(o.amount),2) AS mx "
            "FROM orders o JOIN products p ON o.product_id = p.product_id "
            "GROUP BY p.category ORDER BY p.category"
        ),
        "mysql_sql": (
            "SELECT p.category, ROUND(MIN(o.amount),2) AS mn, ROUND(AVG(o.amount),2) AS av, "
            "ROUND(MAX(o.amount),2) AS mx "
            "FROM orders o JOIN products p ON o.product_id = p.product_id "
            "GROUP BY p.category ORDER BY p.category"
        ),
    },
    {
        "query_id": "Q13_top10_products_by_qty",
        "description": "Top 10 products by total quantity sold",
        "category": "join/group-by/limit",
        "postgres_sql": (
            "SELECT o.product_id, SUM(o.quantity) AS qty "
            "FROM orders o GROUP BY o.product_id "
            "ORDER BY qty DESC, o.product_id ASC LIMIT 10"
        ),
        "mysql_sql": (
            "SELECT o.product_id, SUM(o.quantity) AS qty "
            "FROM orders o GROUP BY o.product_id "
            "ORDER BY qty DESC, o.product_id ASC LIMIT 10"
        ),
    },
    {
        "query_id": "Q14_orders_in_h1_2024",
        "description": "Orders placed in H1 2024 (date range filter)",
        "category": "filter/date-range",
        "postgres_sql": (
            "SELECT COUNT(*) AS c FROM orders "
            "WHERE order_ts >= TIMESTAMP '2024-01-01 00:00:00' "
            "AND order_ts < TIMESTAMP '2024-07-01 00:00:00'"
        ),
        "mysql_sql": (
            "SELECT COUNT(*) AS c FROM orders "
            "WHERE order_ts >= '2024-01-01 00:00:00' "
            "AND order_ts < '2024-07-01 00:00:00'"
        ),
    },
    {
        "query_id": "Q15_customers_name_starts_a",
        "description": "Customers whose name starts with 'A' (LIKE)",
        "category": "filter/string-like",
        "postgres_sql": "SELECT COUNT(*) AS c FROM customers WHERE full_name LIKE 'A%'",
        "mysql_sql":    "SELECT COUNT(*) AS c FROM customers WHERE full_name LIKE 'A%'",
    },
    {
        "query_id": "Q16_high_value_orders",
        "description": "Count of high value orders (amount > 500)",
        "category": "filter/threshold",
        "postgres_sql": "SELECT COUNT(*) AS c FROM orders WHERE amount > 500",
        "mysql_sql":    "SELECT COUNT(*) AS c FROM orders WHERE amount > 500",
    },
    {
        "query_id": "Q17_revenue_by_year",
        "description": "Revenue per year (year extraction dialects)",
        "category": "date-func/group-by",
        "postgres_sql": (
            "SELECT CAST(EXTRACT(YEAR FROM order_ts) AS INTEGER) AS yr, "
            "ROUND(SUM(amount), 2) AS revenue "
            "FROM orders GROUP BY CAST(EXTRACT(YEAR FROM order_ts) AS INTEGER) ORDER BY yr"
        ),
        "mysql_sql": (
            "SELECT YEAR(order_ts) AS yr, ROUND(SUM(amount), 2) AS revenue "
            "FROM orders GROUP BY YEAR(order_ts) ORDER BY yr"
        ),
    },
    {
        "query_id": "Q18_distinct_categories",
        "description": "Number of distinct product categories",
        "category": "aggregate/distinct",
        "postgres_sql": "SELECT COUNT(DISTINCT category) AS c FROM products",
        "mysql_sql":    "SELECT COUNT(DISTINCT category) AS c FROM products",
    },
    {
        "query_id": "Q19_avg_qty_by_status",
        "description": "Average quantity per order status (HAVING filter)",
        "category": "group-by/having",
        "postgres_sql": (
            "SELECT order_status, ROUND(AVG(quantity), 4) AS avg_qty "
            "FROM orders GROUP BY order_status HAVING COUNT(*) > 0 "
            "ORDER BY order_status"
        ),
        "mysql_sql": (
            "SELECT order_status, ROUND(AVG(quantity), 4) AS avg_qty "
            "FROM orders GROUP BY order_status HAVING COUNT(*) > 0 "
            "ORDER BY order_status"
        ),
    },
    {
        "query_id": "Q20_distinct_country_segment",
        "description": "Distinct country-segment combinations (string concat dialects)",
        "category": "distinct/string-concat",
        "postgres_sql": (
            "SELECT DISTINCT (country || '-' || segment) AS combo "
            "FROM customers ORDER BY combo"
        ),
        "mysql_sql": (
            "SELECT DISTINCT CONCAT(country, '-', segment) AS combo "
            "FROM customers ORDER BY combo"
        ),
    },
]

assert len(QUERY_PAIRS) == 20, "expected exactly 20 query pairs"
assert len({q["query_id"] for q in QUERY_PAIRS}) == 20, "query_id values must be unique"
