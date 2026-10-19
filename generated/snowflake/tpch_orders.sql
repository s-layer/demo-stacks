CREATE OR REPLACE SEMANTIC VIEW tpch_orders
TABLES (
    o AS SNOWFLAKE_SAMPLE_DATA.TPCH_SF10.orders PRIMARY KEY (o_orderkey) COMMENT = 'One row per order.',
    c AS SNOWFLAKE_SAMPLE_DATA.TPCH_SF10.customer PRIMARY KEY (c_custkey),
    n AS SNOWFLAKE_SAMPLE_DATA.TPCH_SF10.nation PRIMARY KEY (n_nationkey),
    r AS SNOWFLAKE_SAMPLE_DATA.TPCH_SF10.region PRIMARY KEY (r_regionkey)
)
RELATIONSHIPS (
    order_customer AS o(o_custkey) REFERENCES c,
    customer_nation AS c(c_nationkey) REFERENCES n,
    nation_region AS n(n_regionkey) REFERENCES r
)
DIMENSIONS (
    o.order_date AS o.o_orderdate,
    o.order_year AS year(o.o_orderdate),
    o.order_month AS date_trunc('month', o.o_orderdate),
    o.order_priority AS o.o_orderpriority,
    o.order_status AS o.o_orderstatus,
    c.market_segment AS c.c_mktsegment,
    n.customer_nation AS n.n_name,
    r.customer_region AS r.r_name
)
METRICS (
    o.order_count AS count(*),
    o.customer_count AS count(DISTINCT o.o_custkey),
    o.total_price AS sum(o.o_totalprice),
    o.avg_order_value AS avg(o.o_totalprice),
    orders_per_customer AS o.order_count / o.customer_count
)
COMMENT = 'Orders and order value by customer and geography.';
