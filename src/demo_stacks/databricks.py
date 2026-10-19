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

    from demo_stacks import semantic


class Cursor(Protocol):
    """The slice of a DBAPI cursor this module uses."""

    def execute(self, operation: str) -> Any: ...

    def fetchall(self) -> Sequence[Any]: ...


def schema(catalog: str, sf: float) -> str:
    """``catalog.tpch_sf<N>``: where the tables and metric views for ``sf`` live."""
    return f"{catalog}.{dataset.schema_name(sf)}"


def load_statements(
    staging_uri: str, schema: str, *, tables: Iterable[str] = dataset.TABLES
) -> list[str]:
    """
    One CTAS per table, reading ``<staging_uri>/<table>/*.parquet``.

    ``CLUSTER BY AUTO`` hands clustering to predictive optimization, which is what
    the Lakehouse//RT guidance asks for on the tables it serves.
    """
    base = staging_uri.rstrip("/")
    return [
        f"CREATE OR REPLACE TABLE {schema}.{table} CLUSTER BY AUTO AS "
        f"SELECT * FROM read_files({sql_string(f'{base}/{table}/')}, format => 'parquet')"
        for table in tables
    ]


def count_rows(
    cursor: Cursor, schema: str, tables: Iterable[str] = dataset.TABLES
) -> dict[str, int]:
    """Row count per table, in one round trip."""
    sql = " UNION ALL ".join(
        f"SELECT {sql_string(t)} AS t, count(*) AS n FROM {schema}.{t}" for t in tables
    )
    cursor.execute(sql)
    return {str(row[0]): int(row[1]) for row in cursor.fetchall()}


def load(cursor: Cursor, staging_uri: str, sf: float, *, catalog: str) -> dict[str, int]:
    """
    Replace the TPC-H tables for ``sf`` from staged Parquet and check their row counts.

    Raises:
        ValueError: If a table's row count disagrees with the TPC-H spec for ``sf``,
            which means the staged Parquet is not one complete run at that scale.
    """
    target = schema(catalog, sf)
    for stmt in load_statements(staging_uri, target):
        cursor.execute(stmt)
    counts = count_rows(cursor, target)
    dataset.check_counts(sf, counts)
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


def connect(http_path: str) -> AbstractContextManager[Cursor]:
    """
    A cursor on the warehouse at ``http_path``, over the Statement Execution API.

    Authenticates with ``DATABRICKS_TOKEN`` when it is set, otherwise with the
    browser OAuth flow.
    """
    from contextlib import contextmanager

    from adbc_poolhouse import DatabricksPythonConfig, close_pool, create_pool
    from pydantic import SecretStr

    token = os.environ.get("DATABRICKS_TOKEN")
    config = DatabricksPythonConfig(
        host=host_from_env(),
        http_path=http_path,
        token=SecretStr(token) if token else None,
        auth_type=None if token else "OAuthU2M",
    )

    @contextmanager
    def cursor() -> Generator[Cursor]:
        pool = create_pool(config)
        try:
            with pool.connect() as conn:
                yield conn.cursor()
        finally:
            close_pool(pool)

    return cursor()
