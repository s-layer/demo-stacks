"""Loading and metric view DDL for the Lakehouse//RT stack, against a recording cursor."""

from __future__ import annotations

import contextlib
import re
from typing import TYPE_CHECKING, Any

import adbc_poolhouse
import duckdb
import pytest
import yaml

from demo_stacks import cli, dataset
from demo_stacks import databricks as dbx

from .conftest import SF

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from demo_stacks import semantic

STAGING = "s3://bucket/staging/tpch_sf1"


class FakeCursor:
    """Records statements; answers the row-count query with ``counts``."""

    def __init__(self, counts: dict[str, int] | None = None) -> None:
        self.sql: list[str] = []
        self.counts = counts if counts is not None else full_counts(1)
        self.rows: list[tuple[Any, ...]] = []

    def execute(self, operation: str) -> None:
        self.sql.append(operation)
        if " UNION ALL " in operation:
            tables = re.findall(r"SELECT '(\w+)' AS t", operation)
            self.rows = [(t, self.counts[t]) for t in tables]
        else:
            self.rows = [("row",)]

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows


def full_counts(sf: float) -> dict[str, int]:
    orders = dataset.expected_rows(sf)["orders"]
    return dataset.expected_rows(sf) | {
        "lineitem": round(6_001_215 * sf),
        "orders_in_lineitem": orders,
    }


def test_each_table_loads_under_a_side_name_from_its_staged_parquet() -> None:
    stmts = dbx.load_statements(STAGING + "/", "cat.tpch_sf1", tables=["orders"])
    assert stmts == [
        "CREATE OR REPLACE TABLE cat.tpch_sf1.orders__load CLUSTER BY AUTO AS SELECT * FROM "
        "read_files('s3://bucket/staging/tpch_sf1/orders/', format => 'parquet')"
    ]
    assert len(dbx.load_statements(STAGING, "cat.tpch_sf1")) == len(dataset.TABLES)


def test_load_checks_everything_before_touching_the_live_tables() -> None:
    cursor = FakeCursor()
    assert dbx.load(cursor, STAGING, 1, catalog="cat") == full_counts(1)
    n = len(dataset.TABLES)
    ctas, (count_sql,), swaps = cursor.sql[:n], cursor.sql[n : n + 1], cursor.sql[n + 1 :]
    assert ctas == dbx.load_statements(STAGING, "cat.tpch_sf1")
    assert "FROM cat.tpch_sf1.lineitem__load" in count_sql
    assert swaps == dbx.swap_statements("cat.tpch_sf1")
    assert swaps[:2] == [
        "CREATE OR REPLACE TABLE cat.tpch_sf1.region DEEP CLONE cat.tpch_sf1.region__load",
        "DROP TABLE cat.tpch_sf1.region__load",
    ]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"orders": 1_499_999}, "orders: got 1499999, expected 1500000"),
        ({"orders_in_lineitem": 1_350_000}, "lineitem is incomplete: it covers 1350000 of"),
    ],
)
def test_a_failed_check_leaves_the_live_tables_alone(change: dict[str, int], message: str) -> None:
    cursor = FakeCursor(full_counts(1) | change)
    with pytest.raises(ValueError, match=message):
        dbx.load(cursor, STAGING, 1, catalog="cat")
    after_check = cursor.sql[len(dataset.TABLES) + 1 :]
    assert after_check == [f"DROP TABLE IF EXISTS cat.tpch_sf1.{t}__load" for t in dataset.TABLES]


def test_lineitem_check_catches_a_missing_slice(tmp_path: Path) -> None:
    """
    The guard's premise, on real dbgen output: slices keep an order's lines together.

    Runs the same count query on DuckDB over a 3-slice SF0.01 run, whole and with one
    lineitem slice missing.
    """
    dataset.generate(SF, tmp_path, chunks=3)
    con = duckdb.connect()
    dataset.load(con, tmp_path, schema="whole")
    dbx.check_load(SF, dbx.count_rows(con, "whole"))

    (tmp_path / "lineitem" / "part-00001.parquet").unlink()
    dataset.load(con, tmp_path, schema="short")
    counts = dbx.count_rows(con, "short")
    assert dataset.check_counts(SF, counts) is None, "the spec counts alone miss it"
    with pytest.raises(ValueError, match="lineitem is incomplete"):
        dbx.check_load(SF, counts)


def test_metric_views_are_created_in_the_scale_factor_schema(defs: semantic.Definitions) -> None:
    cursor = FakeCursor()
    names = dbx.create_views(cursor, defs, 10, catalog="cat")
    assert names == ["cat.tpch_sf10.tpch_sales", "cat.tpch_sf10.tpch_orders"]
    for stmt, view in zip(cursor.sql, defs.views, strict=True):
        head, _, body = stmt.partition("AS $$\n")
        assert (
            head
            == f"CREATE OR REPLACE VIEW cat.tpch_sf10.{view.name}\nWITH METRICS\nLANGUAGE YAML\n"
        )
        assert body.endswith("$$"), "the Statement Execution API takes no terminator"
        source = yaml.safe_load(body.removesuffix("$$"))["source"]
        assert source == f"cat.tpch_sf10.{view.root.table}"


def test_sample_query_names_real_fields(defs: semantic.Definitions) -> None:
    sql = dbx.sample_query("cat", 1)
    sales = defs.view("tpch_sales")
    dims = {d.name for d in sales.dimensions}
    metrics = {m.name for m in sales.metrics}
    assert {"customer_region", "order_year"} <= dims
    for m in re.findall(r"MEASURE\((\w+)\)", sql):
        assert m in metrics
    assert "FROM cat.tpch_sf1.tpch_sales " in sql


@pytest.mark.parametrize(
    "value", ["https://dbc-1.cloud.databricks.com/", "dbc-1.cloud.databricks.com"]
)
def test_host_comes_from_the_cli_variable_without_scheme(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", value)
    assert dbx.host_from_env() == "dbc-1.cloud.databricks.com"


def test_missing_host_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABRICKS_HOST", raising=False)
    with pytest.raises(SystemExit, match="DATABRICKS_HOST"):
        dbx.host_from_env()


class FakePool:
    def __init__(self, config: Any) -> None:
        self.config = config
        self.closed = False

    @contextlib.contextmanager
    def connect(self) -> Generator[Any]:
        class Conn:
            def cursor(self) -> FakeCursor:
                return FakeCursor()

        yield Conn()


@pytest.mark.parametrize("token", ["dapi-test", None])
def test_connect_uses_the_statement_execution_backend(
    monkeypatch: pytest.MonkeyPatch, token: str | None
) -> None:
    pools: list[FakePool] = []

    def create_pool(config: Any) -> FakePool:
        pools.append(FakePool(config))
        return pools[-1]

    def close_pool(pool: FakePool) -> None:
        pool.closed = True

    monkeypatch.setattr(adbc_poolhouse, "create_pool", create_pool)
    monkeypatch.setattr(adbc_poolhouse, "close_pool", close_pool)
    monkeypatch.setenv("DATABRICKS_HOST", "https://dbc-1.cloud.databricks.com")
    if token:
        monkeypatch.setenv("DATABRICKS_TOKEN", token)
    else:
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)

    with dbx.connect("/sql/1.0/warehouses/abc") as cursor:
        assert isinstance(cursor, FakeCursor)
    (pool,) = pools
    config = pool.config
    assert isinstance(config, adbc_poolhouse.DatabricksPythonConfig)
    assert config.use_kernel, "Lakehouse//RT rejects Thrift"
    assert (config.host, config.http_path) == (
        "dbc-1.cloud.databricks.com",
        "/sql/1.0/warehouses/abc",
    )
    if token:
        assert config.token is not None and config.token.get_secret_value() == token
    else:
        assert config.auth_type == "OAuthU2M"
    assert pool.closed


@pytest.fixture
def cursor(monkeypatch: pytest.MonkeyPatch) -> FakeCursor:
    fake = FakeCursor()

    @contextlib.contextmanager
    def connect(http_path: str) -> Generator[FakeCursor]:
        assert http_path == "/sql/1.0/warehouses/abc"
        yield fake

    monkeypatch.setattr(dbx, "connect", connect)
    return fake


def test_dbx_load_reads_the_scale_factor_prefix(
    cursor: FakeCursor, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["dbx-load", "--sf", "1", "--staging", "s3://bucket/staging/", "--catalog", "cat"]
    assert cli.main([*argv, "--http-path", "/sql/1.0/warehouses/abc"]) == 0
    assert "read_files('s3://bucket/staging/tpch_sf1/lineitem/'" in cursor.sql[7]
    assert cursor.sql[-1].startswith("CREATE OR REPLACE VIEW cat.tpch_sf1.tpch_orders")
    out = capsys.readouterr().out
    assert "loaded cat.tpch_sf1.lineitem: 6,001,215 rows" in out
    assert "created metric view cat.tpch_sf1.tpch_sales" in out


def test_dbx_load_views_only_skips_loading(cursor: FakeCursor) -> None:
    argv = ["dbx-load", "--sf", "1", "--catalog", "cat", "--views-only"]
    assert cli.main([*argv, "--http-path", "/sql/1.0/warehouses/abc"]) == 0
    assert all(s.startswith("CREATE OR REPLACE VIEW") for s in cursor.sql)


def test_dbx_load_needs_staging_to_load(cursor: FakeCursor) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["dbx-load", "--sf", "1", "--catalog", "cat", "--http-path", "x"])
    assert exc.value.code == 2
    assert cursor.sql == []


def test_dbx_query_runs_the_sample_by_default(
    cursor: FakeCursor, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["dbx-query", "--sf", "1", "--catalog", "cat", "--http-path", "/sql/1.0/warehouses/abc"]
    assert cli.main(argv) == 0
    assert cursor.sql == [dbx.sample_query("cat", 1)]
    assert capsys.readouterr().out == "row\n"
    assert cli.main([*argv, "--sql", "SELECT 1"]) == 0
    assert cursor.sql[-1] == "SELECT 1"
