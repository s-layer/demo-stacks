"""
Load TPC-H into Unity Catalog and create the metric views, for the Lakehouse//RT stack.

Lakehouse//RT is read-only and reads only Unity Catalog managed tables, so loading
happens on an ordinary serverless SQL warehouse (the stack's loader): each table is
created with ``CREATE OR REPLACE TABLE ... AS SELECT * FROM read_files(...)`` from the
Parquet that :func:`demo_stacks.dataset.generate` wrote and ``just dbx-stage``
uploaded. The metric views are DDL too, so they are created there as well. Queries
then go to the RT warehouse.

Everything here talks to a DBAPI cursor, so the SQL can be checked without a
workspace; :func:`connect` builds the real one through adbc-poolhouse's
``DatabricksPythonConfig``, the Statement Execution path RT requires.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Protocol

from demo_stacks import dataset
from demo_stacks.render import databricks as render_databricks
from demo_stacks.render import sql_string

if TYPE_CHECKING:
    from collections.abc import Generator, Iterable, Sequence
    from contextlib import AbstractContextManager
    from types import ModuleType

    from adbc_poolhouse import DatabricksPythonConfig
    from semolina.engines.base import Engine
    from semolina.query import Query

    from demo_stacks import semantic


class Cursor(Protocol):
    """The slice of a DBAPI cursor this module uses."""

    def execute(self, operation: str, /) -> Any: ...

    def fetchall(self) -> Sequence[Any]: ...


def schema(catalog: str, sf: float) -> str:
    """``catalog.tpch_sf<N>``: where the tables and metric views for ``sf`` live."""
    return f"{catalog}.{dataset.schema_name(sf)}"


LOAD_SUFFIX = "__load"
"""Tables are loaded under ``<table>__load`` and swapped in only once all check out."""


def load_statements(
    staging_uri: str, schema: str, *, tables: Iterable[str] = dataset.TABLES
) -> list[str]:
    """
    One CTAS per table into ``<table>__load``, reading ``<staging_uri>/<table>/*.parquet``.

    ``CLUSTER BY AUTO`` hands clustering to predictive optimization, which is what
    the Lakehouse//RT guidance asks for on the tables it serves.
    """
    base = staging_uri.rstrip("/")
    return [
        f"CREATE OR REPLACE TABLE {schema}.{table}{LOAD_SUFFIX} CLUSTER BY AUTO AS "
        f"SELECT * FROM read_files({sql_string(f'{base}/{table}/')}, format => 'parquet')"
        for table in tables
    ]


def swap_statements(schema: str, *, tables: Iterable[str] = dataset.TABLES) -> list[str]:
    """
    Replace each live table with its checked ``<table>__load`` copy, then drop the copy.

    ``CREATE OR REPLACE ... DEEP CLONE`` swaps a table in one atomic step, so a query
    on RT never finds it missing. The tables still switch over one at a time.
    """
    stmts: list[str] = []
    for t in tables:
        stmts += [
            f"CREATE OR REPLACE TABLE {schema}.{t} DEEP CLONE {schema}.{t}{LOAD_SUFFIX}",
            f"DROP TABLE {schema}.{t}{LOAD_SUFFIX}",
        ]
    return stmts


def count_rows(
    cursor: Cursor, schema: str, tables: Iterable[str] = dataset.TABLES, *, suffix: str = ""
) -> dict[str, int]:
    """
    Row count per table, in one round trip.

    Also returns ``orders_in_lineitem``, the number of distinct orders lineitem
    refers to. The spec fixes no exact lineitem count, but every order has line
    items and ``dbgen`` slices keep an order's lines together, so a missing lineitem
    slice shows up as this falling short of the ``orders`` count.
    """
    parts = [
        f"SELECT {sql_string(t)} AS t, count(*) AS n FROM {schema}.{t}{suffix}" for t in tables
    ]
    if "lineitem" in tables:
        parts.append(
            "SELECT 'orders_in_lineitem' AS t, count(DISTINCT l_orderkey) AS n "
            f"FROM {schema}.lineitem{suffix}"
        )
    cursor.execute(" UNION ALL ".join(parts))
    return {str(row[0]): int(row[1]) for row in cursor.fetchall()}


def check_load(sf: float, counts: dict[str, int]) -> None:
    """Raise unless ``counts`` (from :func:`count_rows`) are one complete TPC-H run at ``sf``."""
    dataset.check_counts(sf, counts)
    if counts["orders_in_lineitem"] != counts["orders"]:
        raise ValueError(
            f"TPC-H SF{sf} lineitem is incomplete: it covers {counts['orders_in_lineitem']} "
            f"of {counts['orders']} orders"
        )


def load(cursor: Cursor, staging_uri: str, sf: float, *, catalog: str) -> dict[str, int]:
    """
    Replace the TPC-H tables for ``sf`` from staged Parquet, only if all of it checks out.

    Every table is loaded under ``<table>__load`` first and checked against the
    spec. Only then do the live tables, which the metric views and RT read, get
    replaced, so a partial or stale staging upload leaves them as they were. Each
    replacement is atomic, but they happen one table at a time, so a run that dies
    mid-swap leaves some tables new and some old; rerunning the load finishes it.

    Returns:
        Rows per table, plus ``orders_in_lineitem`` (see :func:`count_rows`).

    Raises:
        ValueError: If the staged Parquet is not one complete run at ``sf``.
    """
    target = schema(catalog, sf)
    for stmt in load_statements(staging_uri, target):
        cursor.execute(stmt)
    counts = count_rows(cursor, target, suffix=LOAD_SUFFIX)
    try:
        check_load(sf, counts)
    except ValueError:
        for t in dataset.TABLES:
            cursor.execute(f"DROP TABLE IF EXISTS {target}.{t}{LOAD_SUFFIX}")
        raise
    for stmt in swap_statements(target):
        cursor.execute(stmt)
    return counts


def view_statements(defs: semantic.Definitions, schema: str) -> list[str]:
    """``CREATE OR REPLACE VIEW ... WITH METRICS`` for every canonical view, in ``schema``."""
    # The Statement Execution API takes one statement without a terminator.
    return [
        render_databricks.render(v, schema=schema).rstrip().removesuffix(";") for v in defs.views
    ]


def create_views(
    cursor: Cursor, defs: semantic.Definitions, sf: float, *, catalog: str
) -> list[str]:
    """Create the metric views for ``sf``; returns their qualified names."""
    target = schema(catalog, sf)
    for stmt in view_statements(defs, target):
        cursor.execute(stmt)
    return [f"{target}.{v.name}" for v in defs.views]


def sample_query(catalog: str, sf: float) -> str:
    """The README's example metric view query: revenue and profit by region and year."""
    return (
        "SELECT customer_region, order_year, MEASURE(net_revenue) AS net_revenue, "
        f"MEASURE(profit) AS profit FROM {schema(catalog, sf)}.tpch_sales "
        "GROUP BY ALL ORDER BY ALL"
    )


def host_from_env() -> str:
    """
    The workspace hostname from ``DATABRICKS_HOST``, as the Databricks CLI and Terraform use it.

    The connector wants a bare hostname, so a scheme or trailing slash is dropped.
    """
    host = os.environ.get("DATABRICKS_HOST", "")
    host = host.removeprefix("https://").removeprefix("http://").rstrip("/")
    if not host:
        raise SystemExit("set DATABRICKS_HOST to the workspace URL")
    return host


def config(
    http_path: str, *, catalog: str | None = None, schema: str | None = None
) -> DatabricksPythonConfig:
    """
    The adbc-poolhouse config for the warehouse at ``http_path``.

    Uses ``DATABRICKS_TOKEN`` when it is set, otherwise the browser OAuth flow.
    ``catalog`` and ``schema`` set the default namespace, which is how semolina's
    unqualified view names find ``catalog.tpch_sf<N>``.
    """
    from adbc_poolhouse import DatabricksPythonConfig
    from pydantic import SecretStr

    token = os.environ.get("DATABRICKS_TOKEN")
    return DatabricksPythonConfig(
        host=host_from_env(),
        http_path=http_path,
        token=SecretStr(token) if token else None,
        auth_type=None if token else "OAuthU2M",
        catalog=catalog,
        schema=schema,
    )


def connect(http_path: str) -> AbstractContextManager[Cursor]:
    """A cursor on the warehouse at ``http_path``, over the Statement Execution API."""
    from contextlib import contextmanager

    from adbc_poolhouse import close_pool, create_pool

    cfg = config(http_path)

    @contextmanager
    def cursor() -> Generator[Cursor]:
        pool = create_pool(cfg)
        try:
            with pool.connect() as conn:
                yield conn.cursor()
        finally:
            close_pool(pool)

    return cursor()


def semolina_sample(models: ModuleType) -> Query:
    """:func:`sample_query` written with the generated semolina models."""
    sales = models.TpchSales
    return (
        sales.query()
        .metrics(sales.net_revenue, sales.profit)
        .dimensions(sales.customer_region, sales.order_year)
        .order_by(sales.customer_region, sales.order_year)
    )


def semolina_engine(http_path: str, catalog: str, sf: float) -> Engine:
    """A registered semolina engine whose default namespace is ``catalog.tpch_sf<N>``."""
    import semolina

    return semolina.create_engine(
        config(http_path, catalog=catalog, schema=dataset.schema_name(sf)), register=True
    )
