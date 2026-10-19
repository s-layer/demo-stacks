CREATE OR REPLACE VIEW s_layer_demo.tpch_sf10.tpch_sales
WITH METRICS
LANGUAGE YAML
AS $$
version: 1.1
comment: 'Line-item sales: revenue, discounts, quantities and supplier profit by order, customer, part, supplier and geography.'
source: s_layer_demo.tpch_sf10.lineitem
joins:
- name: o
  source: s_layer_demo.tpch_sf10.orders
  'on': source.l_orderkey = o.o_orderkey
  joins:
  - name: c
    source: s_layer_demo.tpch_sf10.customer
    'on': o.o_custkey = c.c_custkey
    joins:
    - name: cn
      source: s_layer_demo.tpch_sf10.nation
      'on': c.c_nationkey = cn.n_nationkey
      joins:
      - name: cr
        source: s_layer_demo.tpch_sf10.region
        'on': cn.n_regionkey = cr.r_regionkey
- name: p
  source: s_layer_demo.tpch_sf10.part
  'on': source.l_partkey = p.p_partkey
- name: s
  source: s_layer_demo.tpch_sf10.supplier
  'on': source.l_suppkey = s.s_suppkey
  joins:
  - name: sn
    source: s_layer_demo.tpch_sf10.nation
    'on': s.s_nationkey = sn.n_nationkey
    joins:
    - name: sr
      source: s_layer_demo.tpch_sf10.region
      'on': sn.n_regionkey = sr.r_regionkey
- name: ps
  source: s_layer_demo.tpch_sf10.partsupp
  'on': source.l_partkey = ps.ps_partkey AND source.l_suppkey = ps.ps_suppkey
dimensions:
- name: order_date
  expr: o.o_orderdate
- name: order_year
  expr: year(o.o_orderdate)
- name: order_month
  expr: date_trunc('month', o.o_orderdate)
- name: order_priority
  expr: o.o_orderpriority
- name: order_status
  expr: o.o_orderstatus
- name: ship_date
  expr: source.l_shipdate
- name: ship_year
  expr: year(source.l_shipdate)
- name: ship_mode
  expr: source.l_shipmode
- name: return_flag
  expr: source.l_returnflag
- name: line_status
  expr: source.l_linestatus
- name: market_segment
  expr: o.c.c_mktsegment
- name: customer_nation
  expr: o.c.cn.n_name
- name: customer_region
  expr: o.c.cn.cr.r_name
- name: supplier_nation
  expr: s.sn.n_name
- name: supplier_region
  expr: s.sn.sr.r_name
- name: part_brand
  expr: p.p_brand
- name: part_type
  expr: p.p_type
- name: part_container
  expr: p.p_container
- name: part_size
  expr: p.p_size
measures:
- name: line_count
  expr: count(*)
- name: order_count
  expr: count(DISTINCT source.l_orderkey)
- name: quantity
  expr: sum(source.l_quantity)
- name: gross_revenue
  expr: sum(source.l_extendedprice)
  comment: List price before discount.
- name: net_revenue
  expr: sum(source.l_extendedprice * (1 - source.l_discount))
  comment: Revenue after discount (TPC-H Q5 "revenue").
- name: charged_amount
  expr: sum(source.l_extendedprice * (1 - source.l_discount) * (1 + source.l_tax))
  comment: Net revenue plus tax (TPC-H Q1 "sum_charge").
- name: avg_discount
  expr: avg(source.l_discount)
- name: avg_price
  expr: avg(source.l_extendedprice)
- name: supply_cost
  expr: sum((ps.ps_supplycost) * source.l_quantity)
  comment: What the supplier charged for the parts sold (TPC-H Q9).
- name: profit
  expr: MEASURE(net_revenue) - MEASURE(supply_cost)
  comment: Net revenue less supply cost (TPC-H Q9 "sum_profit").
$$;
