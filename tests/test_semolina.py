"""The generated Semolina models query the DuckDB views end to end."""

from __future__ import annotations

import importlib.util
from typing import TYPE_CHECKING

import pytest
import semolina
from adbc_poolhouse import DuckDBConfig

from demo_stacks import cli

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path
    from types import ModuleType

    import duckdb
    from semolina.engines.base import Engine


@pytest.fixture
def models() -> ModuleType:
    path = cli.GENERATED / "semolina" / "tpch_models.py"
    spec = importlib.util.spec_from_file_location("tpch_models", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def engine(warehouse: Path) -> Iterator[Engine]:
    with semolina.create_engine(
        DuckDBConfig(database=str(warehouse), read_only=True, pool_size=1), register=True
    ) as engine:
        yield engine


def test_revenue_by_region_through_semolina(
    engine: Engine, models: ModuleType, con: duckdb.DuckDBPyConnection
) -> None:
    sales = models.TpchSales
    query = sales.query().metrics(sales.net_revenue).dimensions(sales.customer_region)
    with query.execute() as cursor:
        got = sorted(
            (r.customer_region, round(float(r.net_revenue), 4)) for r in cursor.fetchall_rows()
        )
    want = sorted(
        (name, round(float(v), 4))
        for name, v in con.execute(
            """
            SELECT r.r_name, sum(l.l_extendedprice * (1 - l.l_discount))
            FROM tpch.lineitem l
            JOIN tpch.orders o ON l.l_orderkey = o.o_orderkey
            JOIN tpch.customer c ON o.o_custkey = c.c_custkey
            JOIN tpch.nation n ON c.c_nationkey = n.n_nationkey
            JOIN tpch.region r ON n.n_regionkey = r.r_regionkey
            GROUP BY ALL
            """
        ).fetchall()
    )
    assert len(want) == 5
    assert got == want
