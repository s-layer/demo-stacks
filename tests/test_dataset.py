"""TPC-H generation and loading."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import duckdb
import pytest

from demo_stacks import dataset

from .conftest import SF

if TYPE_CHECKING:
    from pathlib import Path


def scalar(con: duckdb.DuckDBPyConnection, sql: str) -> Any:
    row = con.execute(sql).fetchone()
    assert row is not None
    return row[0]


def table_fingerprints(con: duckdb.DuckDBPyConnection, schema: str) -> dict[str, tuple[Any, ...]]:
    """Row count plus an order-independent hash of every row, per table."""
    fingerprints: dict[str, tuple[Any, ...]] = {}
    for t in dataset.TABLES:
        row = con.execute(f"SELECT count(*), sum(hash(COLUMNS(*))) FROM {schema}.{t}").fetchone()
        assert row is not None
        fingerprints[t] = row
    return fingerprints


def test_expected_rows_follow_the_spec() -> None:
    assert dataset.expected_rows(1) == {
        "region": 5,
        "nation": 25,
        "supplier": 10_000,
        "customer": 150_000,
        "part": 200_000,
        "partsupp": 800_000,
        "orders": 1_500_000,
    }
    # A float SF must not truncate: 0.29 x 10,000 is 2899.999... in binary.
    assert dataset.expected_rows(0.29)["supplier"] == 2_900


def test_generated_parquet_has_spec_row_counts(parquet_dir: Path) -> None:
    con = duckdb.connect()
    counts = {
        t: int(scalar(con, f"SELECT count(*) FROM '{parquet_dir}/{t}/*.parquet'"))
        for t in dataset.TABLES
    }
    dataset.check_counts(SF, counts)
    assert counts["lineitem"] > 5.9e6 * SF


def test_chunked_generation_equals_single_pass(tmp_path: Path, parquet_dir: Path) -> None:
    dataset.generate(SF, tmp_path, chunks=3)
    assert len(list((tmp_path / "lineitem").glob("*.parquet"))) == 3
    con = duckdb.connect()
    dataset.load(con, parquet_dir, schema="single")
    dataset.load(con, tmp_path, schema="chunked")
    assert table_fingerprints(con, "chunked") == table_fingerprints(con, "single")


def test_check_counts_rejects_a_short_table() -> None:
    counts = dataset.expected_rows(SF) | {"lineitem": 1}
    counts["orders"] -= 1
    with pytest.raises(ValueError, match="orders: got 14999, expected 15000"):
        dataset.check_counts(SF, counts)


@pytest.mark.parametrize(("sf", "chunks"), [(0, 1), (-1, 1), (1, 0)])
def test_generate_rejects_bad_arguments(tmp_path: Path, sf: float, chunks: int) -> None:
    with pytest.raises(ValueError):
        dataset.generate(sf, tmp_path, chunks=chunks)


def test_load_replaces_existing_tables(parquet_dir: Path) -> None:
    con = duckdb.connect()
    con.execute("CREATE SCHEMA tpch")
    con.execute("CREATE TABLE tpch.region AS SELECT 1 AS stale")
    loaded = dataset.load(con, parquet_dir, schema="tpch", tables=["region"])
    assert loaded == {"region": 5}
    assert "r_name" in [c[0] for c in con.execute("DESCRIBE tpch.region").fetchall()]
