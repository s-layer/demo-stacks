CREATE OR REPLACE VIEW s_layer_demo.tpch.tpch_orders
WITH METRICS
LANGUAGE YAML
AS $$
version: 1.1
comment: Orders and order value by customer and geography.
source: s_layer_demo.tpch.orders
joins:
- name: c
  source: s_layer_demo.tpch.customer
  'on': source.o_custkey = c.c_custkey
  joins:
  - name: n
    source: s_layer_demo.tpch.nation
    'on': c.c_nationkey = n.n_nationkey
    joins:
    - name: r
      source: s_layer_demo.tpch.region
      'on': n.n_regionkey = r.r_regionkey
dimensions:
- name: order_date
  expr: source.o_orderdate
- name: order_year
  expr: year(source.o_orderdate)
- name: order_month
  expr: date_trunc('month', source.o_orderdate)
- name: order_priority
  expr: source.o_orderpriority
- name: order_status
  expr: source.o_orderstatus
- name: market_segment
  expr: c.c_mktsegment
- name: customer_nation
  expr: c.n.n_name
- name: customer_region
  expr: c.n.r.r_name
measures:
- name: order_count
  expr: count(*)
- name: customer_count
  expr: count(DISTINCT source.o_custkey)
- name: total_price
  expr: sum(source.o_totalprice)
- name: avg_order_value
  expr: avg(source.o_totalprice)
- name: orders_per_customer
  expr: MEASURE(order_count) / MEASURE(customer_count)
$$;
