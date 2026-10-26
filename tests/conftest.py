from __future__ import annotations

from typing import TYPE_CHECKING

import duckdb
import pytest

from demo_stacks import dataset, semantic
from demo_stacks.render import duckdb as render_duckdb

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

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
def warehouse(
    tmp_path_factory: pytest.TempPathFactory, parquet_dir: Path, defs: semantic.Definitions
) -> Path:
    """A DuckDB file with the TPC-H tables in schema `tpch` and both semantic views."""
    return build_warehouse(tmp_path_factory.mktemp("wh") / "warehouse.duckdb", parquet_dir, defs)


def build_warehouse(path: Path, parquet_dir: Path, defs: semantic.Definitions) -> Path:
    """Load the Parquet into schema `tpch` of a new DuckDB file and create the views."""
    con = duckdb.connect(str(path))
    try:
        dataset.load(con, parquet_dir, schema="tpch")
        load_semantic_views(con)
        for view in defs.views:
            for stmt in render_duckdb.statements(view, schema="tpch"):
                con.execute(stmt)
    finally:
        con.close()
    return path


@pytest.fixture
def con(warehouse: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    c = duckdb.connect(str(warehouse), read_only=True)
    load_semantic_views(c)
    yield c
    c.close()


def load_semantic_views(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("INSTALL semantic_views FROM community")
    con.execute("LOAD semantic_views")
