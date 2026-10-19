CREATE OR REPLACE SEMANTIC VIEW tpch_sales
TABLES (
    li AS SNOWFLAKE_SAMPLE_DATA.TPCH_SF10.lineitem PRIMARY KEY (l_orderkey, l_linenumber) COMMENT = 'One row per order line.',
    o AS SNOWFLAKE_SAMPLE_DATA.TPCH_SF10.orders PRIMARY KEY (o_orderkey),
    c AS SNOWFLAKE_SAMPLE_DATA.TPCH_SF10.customer PRIMARY KEY (c_custkey),
    cn AS SNOWFLAKE_SAMPLE_DATA.TPCH_SF10.nation PRIMARY KEY (n_nationkey) COMMENT = 'The customer''s nation.',
    cr AS SNOWFLAKE_SAMPLE_DATA.TPCH_SF10.region PRIMARY KEY (r_regionkey) COMMENT = 'The customer''s region.',
    p AS SNOWFLAKE_SAMPLE_DATA.TPCH_SF10.part PRIMARY KEY (p_partkey),
    s AS SNOWFLAKE_SAMPLE_DATA.TPCH_SF10.supplier PRIMARY KEY (s_suppkey),
    sn AS SNOWFLAKE_SAMPLE_DATA.TPCH_SF10.nation PRIMARY KEY (n_nationkey) COMMENT = 'The supplier''s nation.',
    sr AS SNOWFLAKE_SAMPLE_DATA.TPCH_SF10.region PRIMARY KEY (r_regionkey) COMMENT = 'The supplier''s region.',
    ps AS SNOWFLAKE_SAMPLE_DATA.TPCH_SF10.partsupp PRIMARY KEY (ps_partkey, ps_suppkey)
)
RELATIONSHIPS (
    line_order AS li(l_orderkey) REFERENCES o,
    order_customer AS o(o_custkey) REFERENCES c,
    customer_nation AS c(c_nationkey) REFERENCES cn,
    customer_region AS cn(n_regionkey) REFERENCES cr,
    line_part AS li(l_partkey) REFERENCES p,
    line_supplier AS li(l_suppkey) REFERENCES s,
    supplier_nation AS s(s_nationkey) REFERENCES sn,
    supplier_region AS sn(n_regionkey) REFERENCES sr,
    line_partsupp AS li(l_partkey, l_suppkey) REFERENCES ps
)
FACTS (
    ps.unit_supply_cost AS ps.ps_supplycost COMMENT = 'What this supplier charges for one unit of this part.'
)
DIMENSIONS (
    o.order_date AS o.o_orderdate,
    o.order_year AS year(o.o_orderdate),
    o.order_month AS date_trunc('month', o.o_orderdate),
    o.order_priority AS o.o_orderpriority,
    o.order_status AS o.o_orderstatus,
    li.ship_date AS li.l_shipdate,
    li.ship_year AS year(li.l_shipdate),
    li.ship_mode AS li.l_shipmode,
    li.return_flag AS li.l_returnflag,
    li.line_status AS li.l_linestatus,
    c.market_segment AS c.c_mktsegment,
    cn.customer_nation AS cn.n_name,
    cr.customer_region AS cr.r_name,
    sn.supplier_nation AS sn.n_name,
    sr.supplier_region AS sr.r_name,
    p.part_brand AS p.p_brand,
    p.part_type AS p.p_type,
    p.part_container AS p.p_container,
    p.part_size AS p.p_size
)
METRICS (
    li.line_count AS count(*),
    li.order_count AS count(DISTINCT li.l_orderkey),
    li.quantity AS sum(li.l_quantity),
    li.gross_revenue AS sum(li.l_extendedprice) COMMENT = 'List price before discount.',
    li.net_revenue AS sum(li.l_extendedprice * (1 - li.l_discount)) COMMENT = 'Revenue after discount (TPC-H Q5 "revenue").',
    li.charged_amount AS sum(li.l_extendedprice * (1 - li.l_discount) * (1 + li.l_tax)) COMMENT = 'Net revenue plus tax (TPC-H Q1 "sum_charge").',
    li.avg_discount AS avg(li.l_discount),
    li.avg_price AS avg(li.l_extendedprice),
    li.supply_cost AS sum(ps.unit_supply_cost * li.l_quantity) COMMENT = 'What the supplier charged for the parts sold (TPC-H Q9).',
    profit AS li.net_revenue - li.supply_cost COMMENT = 'Net revenue less supply cost (TPC-H Q9 "sum_profit").'
)
COMMENT = 'Line-item sales: revenue, discounts, quantities and supplier profit by order, customer, part, supplier and geography.';
