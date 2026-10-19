"""
Generate the TPC-H dataset at a chosen scale factor and load it into DuckDB catalogs.

Generation uses DuckDB's ``tpch`` extension (``dbgen``). Large scale factors are
generated in ``chunks`` independent slices, each written as its own Parquet file
per table, so memory stays bounded: ``dbgen(sf, children=chunks, step=i)`` yields
slice ``i`` of every table and the slices together are exactly the full dataset.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import duckdb

TABLES = ("region", "nation", "supplier", "customer", "part", "partsupp", "orders", "lineitem")

# Rows per unit of scale factor, from the TPC-H specification (section 4.2.5).
# lineitem is not fixed: it averages about 6,000,000 x SF.
_ROWS_PER_SF = {
    "supplier": 10_000,
    "customer": 150_000,
    "part": 200_000,
    "partsupp": 800_000,
    "orders": 1_500_000,
}
_FIXED_ROWS = {"region": 5, "nation": 25}


def expected_rows(sf: float) -> dict[str, int]:
    """Exact row counts the TPC-H spec fixes for scale factor ``sf`` (all but lineitem)."""
    return {**_FIXED_ROWS, **{t: round(n * sf) for t, n in _ROWS_PER_SF.items()}}


def generate(sf: float, out_dir: Path, *, chunks: int = 1) -> dict[str, int]:
    """
    Write TPC-H at scale factor ``sf`` as Parquet under ``out_dir/<table>/``.

    Args:
        sf: TPC-H scale factor. SF1 is about 1 GB raw, 6M lineitem rows.
        out_dir: Destination; one directory per table, one file per chunk.
        chunks: Number of independent slices to generate. Use more for large SFs
            (roughly one per 10 SF keeps a slice within a few GB of memory).

    Returns:
        Rows written per table.
    """
    if sf <= 0:
        raise ValueError(f"scale factor must be positive, got {sf}")
    if chunks < 1:
        raise ValueError(f"chunks must be at least 1, got {chunks}")
    counts = dict.fromkeys(TABLES, 0)
    for step in range(chunks):
        con = duckdb.connect()
        try:
            con.execute("INSTALL tpch")
            con.execute("LOAD tpch")
            if chunks == 1:
                con.execute("CALL dbgen(sf = ?)", [sf])
            else:
                con.execute("CALL dbgen(sf = ?, children = ?, step = ?)", [sf, chunks, step])
            for table in TABLES:
                dest = out_dir / table
                dest.mkdir(parents=True, exist_ok=True)
                path = dest / f"part-{step:05d}.parquet"
                con.execute(f"COPY {table} TO '{path}' (FORMAT parquet, COMPRESSION zstd)")
                counts[table] += _scalar(con, f"SELECT count(*) FROM {table}")
        finally:
            con.close()
    check_counts(sf, counts)
    return counts


def check_counts(sf: float, counts: dict[str, int]) -> None:
    """Raise if ``counts`` disagree with the row counts the spec fixes for ``sf``."""
    wrong = {t: (counts.get(t), n) for t, n in expected_rows(sf).items() if counts.get(t) != n}
    if wrong:
        detail = ", ".join(f"{t}: got {got}, expected {want}" for t, (got, want) in wrong.items())
        raise ValueError(f"TPC-H SF{sf} row counts are wrong: {detail}")


def load(
    con: duckdb.DuckDBPyConnection,
    parquet_dir: Path | str,
    *,
    schema: str,
    tables: Iterable[str] = TABLES,
) -> dict[str, int]:
    """
    Create each table in ``schema`` from the Parquet that :func:`generate` wrote.

    ``schema`` may live in an attached catalog, e.g. ``"s3t.tpch"`` for an S3 Tables
    bucket attached as ``s3t``. ``parquet_dir`` may be local or an ``s3://`` URL.
    Existing tables are replaced.

    Returns:
        Rows loaded per table.
    """
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    base = str(parquet_dir).rstrip("/")
    loaded = {}
    for table in tables:
        con.execute(f"DROP TABLE IF EXISTS {schema}.{table}")
        con.execute(
            f"CREATE TABLE {schema}.{table} AS "
            f"SELECT * FROM read_parquet('{base}/{table}/*.parquet')"
        )
        loaded[table] = _scalar(con, f"SELECT count(*) FROM {schema}.{table}")
    return loaded


def _scalar(con: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = con.execute(sql).fetchone()
    assert row is not None
    return int(row[0])
