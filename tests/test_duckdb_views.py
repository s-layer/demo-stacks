"""
The rendered DuckDB semantic views return the same numbers as hand-written TPC-H SQL.

Each oracle query is written directly against the base tables, independently of the
canonical definitions, so a wrong expression, a wrong join key or a swapped alias in
the YAML or the renderer shows up as a mismatch.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

import duckdb
import pytest

from demo_stacks import dataset
from demo_stacks.render import duckdb as render_duckdb

from .conftest import load_semantic_views

if TYPE_CHECKING:
    from pathlib import Path

    from demo_stacks import semantic

Rows = list[tuple[Any, ...]]


def semantic_view(
    con: duckdb.DuckDBPyConnection,
    view: str,
    dimensions: list[str],
    metrics: list[str],
    where: str | None = None,
) -> Rows:
    args = [f"dimensions := {dimensions!r}", f"metrics := {metrics!r}"]
    if where:
        args.append(f"where_clause := '{where.replace(chr(39), chr(39) * 2)}'")
    sql = f"SELECT * FROM semantic_view('{view}', {', '.join(args)})"
    cols = ", ".join(f'"{c}"' for c in dimensions + metrics)
    return normalise(con.execute(f"SELECT {cols} FROM ({sql}) ORDER BY ALL").fetchall())


def oracle(con: duckdb.DuckDBPyConnection, sql: str) -> Rows:
    return normalise(con.execute(f"SELECT * FROM ({sql}) ORDER BY ALL").fetchall())


def normalise(rows: Rows) -> Rows:
    """Round numbers so DECIMAL and DOUBLE results of the same value compare equal."""
    return [
        tuple(round(float(v), 4) if isinstance(v, float | Decimal) else v for v in row)
        for row in rows
    ]


def test_pricing_summary_matches_tpch_q1(con: duckdb.DuckDBPyConnection) -> None:
    got = semantic_view(
        con,
        "tpch_sales",
        ["return_flag", "line_status"],
        [
            "quantity",
            "gross_revenue",
            "net_revenue",
            "charged_amount",
            "avg_discount",
            "avg_price",
            "line_count",
        ],
        where="l_shipdate <= DATE '1998-09-02'",
    )
    want = oracle(
        con,
        """
        SELECT l_returnflag, l_linestatus,
               sum(l_quantity), sum(l_extendedprice),
               sum(l_extendedprice * (1 - l_discount)),
               sum(l_extendedprice * (1 - l_discount) * (1 + l_tax)),
               avg(l_discount), avg(l_extendedprice), count(*)
        FROM tpch.lineitem
        WHERE l_shipdate <= DATE '1998-09-02'
        GROUP BY ALL
        """,
    )
    assert len(want) == 4
    assert got == want


def test_revenue_by_customer_region_and_year(con: duckdb.DuckDBPyConnection) -> None:
    got = semantic_view(
        con, "tpch_sales", ["customer_region", "order_year"], ["net_revenue", "order_count"]
    )
    want = oracle(
        con,
        """
        SELECT r.r_name, year(o.o_orderdate),
               sum(l.l_extendedprice * (1 - l.l_discount)), count(DISTINCT l.l_orderkey)
        FROM tpch.lineitem l
        JOIN tpch.orders o ON l.l_orderkey = o.o_orderkey
        JOIN tpch.customer c ON o.o_custkey = c.c_custkey
        JOIN tpch.nation n ON c.c_nationkey = n.n_nationkey
        JOIN tpch.region r ON n.n_regionkey = r.r_regionkey
        GROUP BY ALL
        """,
    )
    assert len(want) == 5 * 7
    assert got == want


def test_customer_and_supplier_nation_are_different_roles(con: duckdb.DuckDBPyConnection) -> None:
    """The two nation aliases must follow their own relationships, not each other's."""
    got = semantic_view(con, "tpch_sales", ["customer_nation", "supplier_nation"], ["line_count"])
    want = oracle(
        con,
        """
        SELECT cn.n_name, sn.n_name, count(*)
        FROM tpch.lineitem l
        JOIN tpch.orders o ON l.l_orderkey = o.o_orderkey
        JOIN tpch.customer c ON o.o_custkey = c.c_custkey
        JOIN tpch.nation cn ON c.c_nationkey = cn.n_nationkey
        JOIN tpch.supplier s ON l.l_suppkey = s.s_suppkey
        JOIN tpch.nation sn ON s.s_nationkey = sn.n_nationkey
        GROUP BY ALL
        """,
    )
    assert any(c != s for c, s, _ in want)
    assert got == want


def test_profit_matches_tpch_q9_shape(con: duckdb.DuckDBPyConnection) -> None:
    got = semantic_view(
        con, "tpch_sales", ["supplier_nation", "order_year"], ["supply_cost", "profit"]
    )
    want = oracle(
        con,
        """
        SELECT n.n_name, year(o.o_orderdate),
               sum(ps.ps_supplycost * l.l_quantity),
               sum(l.l_extendedprice * (1 - l.l_discount) - ps.ps_supplycost * l.l_quantity)
        FROM tpch.lineitem l
        JOIN tpch.partsupp ps ON l.l_partkey = ps.ps_partkey AND l.l_suppkey = ps.ps_suppkey
        JOIN tpch.supplier s ON l.l_suppkey = s.s_suppkey
        JOIN tpch.nation n ON s.s_nationkey = n.n_nationkey
        JOIN tpch.orders o ON l.l_orderkey = o.o_orderkey
        GROUP BY ALL
        """,
    )
    assert got == want


def test_part_and_ship_dimensions(con: duckdb.DuckDBPyConnection) -> None:
    got = semantic_view(con, "tpch_sales", ["part_brand", "ship_mode", "ship_year"], ["quantity"])
    want = oracle(
        con,
        """
        SELECT p.p_brand, l.l_shipmode, year(l.l_shipdate), sum(l.l_quantity)
        FROM tpch.lineitem l JOIN tpch.part p ON l.l_partkey = p.p_partkey
        GROUP BY ALL
        """,
    )
    assert got == want


def test_orders_view_by_segment_and_month(con: duckdb.DuckDBPyConnection) -> None:
    got = semantic_view(
        con,
        "tpch_orders",
        ["market_segment", "order_month"],
        ["order_count", "customer_count", "total_price", "avg_order_value", "orders_per_customer"],
    )
    want = oracle(
        con,
        """
        SELECT c.c_mktsegment, date_trunc('month', o.o_orderdate),
               count(*), count(DISTINCT o.o_custkey), sum(o.o_totalprice),
               avg(o.o_totalprice), count(*) / count(DISTINCT o.o_custkey)
        FROM tpch.orders o JOIN tpch.customer c ON o.o_custkey = c.c_custkey
        GROUP BY ALL
        """,
    )
    assert got == want


def test_orders_view_geography(con: duckdb.DuckDBPyConnection) -> None:
    got = semantic_view(con, "tpch_orders", ["customer_region", "customer_nation"], ["total_price"])
    want = oracle(
        con,
        """
        SELECT r.r_name, n.n_name, sum(o.o_totalprice)
        FROM tpch.orders o
        JOIN tpch.customer c ON o.o_custkey = c.c_custkey
        JOIN tpch.nation n ON c.c_nationkey = n.n_nationkey
        JOIN tpch.region r ON n.n_regionkey = r.r_regionkey
        GROUP BY ALL
        """,
    )
    assert len(want) == 25
    assert got == want


@pytest.mark.parametrize("view_name", ["tpch_sales", "tpch_orders"])
def test_every_dimension_and_metric_is_queryable(
    con: duckdb.DuckDBPyConnection, defs: semantic.Definitions, view_name: str
) -> None:
    view = defs.view(view_name)
    for d in view.dimensions:
        assert semantic_view(con, view_name, [d.name], []), d.name
    for m in view.metrics:
        assert semantic_view(con, view_name, [], [m.name]), m.name


def test_views_over_an_attached_catalog(
    tmp_path: Path, parquet_dir: Path, defs: semantic.Definitions
) -> None:
    """
    The S3 Tables layout: tables in an attached catalog, views in the main database.

    A second DuckDB file stands in for the Iceberg catalog. The views keep working
    after the main database is reopened, provided the catalog is attached again,
    which is why a stack-1 process runs `s3tables.setup_sql` before querying.
    """
    lake, main = tmp_path / "lake.duckdb", tmp_path / "main.duckdb"
    con = duckdb.connect(str(main))
    con.execute(f"ATTACH '{lake}' AS lake")
    dataset.load(con, parquet_dir, schema="lake.tpch")
    load_semantic_views(con)
    for view in defs.views:
        for stmt in render_duckdb.statements(view, schema="lake.tpch"):
            con.execute(stmt)
    con.close()

    con = duckdb.connect(str(main))
    load_semantic_views(con)
    with pytest.raises(duckdb.Error, match="lake"):
        semantic_view(con, "tpch_orders", ["market_segment"], ["order_count"])
    con.execute(f"ATTACH '{lake}' AS lake")
    got = semantic_view(con, "tpch_orders", ["market_segment"], ["order_count"])
    want = oracle(
        con,
        "SELECT c_mktsegment, count(*) FROM lake.tpch.orders "
        "JOIN lake.tpch.customer ON o_custkey = c_custkey GROUP BY ALL",
    )
    assert len(want) == 5
    assert got == want
    con.close()
