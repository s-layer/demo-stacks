"""The `demo-stacks` command line, end to end."""

from __future__ import annotations

from typing import TYPE_CHECKING

import duckdb
import pytest

from demo_stacks import cli

from .conftest import SF, load_semantic_views

if TYPE_CHECKING:
    from pathlib import Path


def test_ddl_prints_one_view_for_the_given_schema(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["ddl", "duckdb", "--schema", "lake.tpch", "--view", "tpch_orders"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("CREATE OR REPLACE SEMANTIC VIEW tpch_orders AS\n")
    assert "o AS lake.tpch.orders PRIMARY KEY" in out
    assert "tpch_sales" not in out


def test_ddl_empty_schema_leaves_tables_unqualified(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["ddl", "snowflake", "--schema", ""]) == 0
    out = capsys.readouterr().out
    assert "    li AS lineitem PRIMARY KEY" in out
    assert "CREATE OR REPLACE SEMANTIC VIEW tpch_orders\n" in out


def test_ddl_defaults_to_the_committed_schema(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["ddl", "databricks", "--view", "tpch_orders"]) == 0
    assert "source: s_layer_demo.tpch_sf10.orders" in capsys.readouterr().out


def test_generate_prints_row_counts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["generate", "--sf", str(SF), "--out", str(tmp_path)]) == 0
    lines = {ln.split()[0]: ln.split()[1] for ln in capsys.readouterr().out.splitlines()}
    assert lines["orders"] == "15,000"
    assert (tmp_path / "lineitem" / "part-00000.parquet").exists()


def test_load_creates_tables_and_queryable_views(
    tmp_path: Path, parquet_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "views.duckdb"
    argv = ["load", "--parquet", str(parquet_dir), "--database", str(db), "--schema", "wh"]
    assert cli.main(argv) == 0
    out = capsys.readouterr().out
    assert "loaded wh.orders: 15,000 rows" in out
    assert "created semantic view tpch_sales" in out

    con = duckdb.connect(str(db))
    load_semantic_views(con)
    row = con.execute(
        "SELECT * FROM semantic_view('tpch_orders', metrics := ['order_count'])"
    ).fetchone()
    assert row == (15_000,)
    con.close()


def test_load_views_only_skips_loading(
    tmp_path: Path, parquet_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "views.duckdb"
    assert cli.main(["load", "--parquet", str(parquet_dir), "--database", str(db)]) == 0
    capsys.readouterr()
    assert cli.main(["load", "--views-only", "--database", str(db)]) == 0
    out = capsys.readouterr().out
    assert "loaded" not in out
    assert out.count("created semantic view") == 2


def test_load_without_parquet_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["load", "--database", ":memory:"])
    assert exc.value.code == 2
