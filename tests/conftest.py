from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from demo_stacks import dataset, semantic
from demo_stacks.render import duckdb as render_duckdb

# Small enough to generate in about a second, large enough that every dimension
# has several members and every join is exercised.
SF = 0.01


@pytest.fixture(scope="session")
def defs() -> semantic.Definitions:
    return semantic.load()


@pytest.fixture(scope="session")
def parquet_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("tpch")
    dataset.generate(SF, out)
    return out


@pytest.fixture(scope="session")
def warehouse(tmp_path_factory: pytest.TempPathFactory, parquet_dir: Path, defs) -> Path:
    """A DuckDB file with the TPC-H tables in schema `tpch` and both semantic views."""
    path = tmp_path_factory.mktemp("wh") / "warehouse.duckdb"
    con = duckdb.connect(str(path))
    dataset.load(con, parquet_dir, schema="tpch")
    load_semantic_views(con)
    for view in defs.views:
        for stmt in render_duckdb.statements(view, schema="tpch"):
            con.execute(stmt)
    con.close()
    return path


@pytest.fixture
def con(warehouse: Path):
    c = duckdb.connect(str(warehouse), read_only=True)
    load_semantic_views(c)
    yield c
    c.close()


def load_semantic_views(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("INSTALL semantic_views FROM community")
    con.execute("LOAD semantic_views")
