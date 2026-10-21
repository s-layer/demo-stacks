"""The benchmark times every path on a real engine, and they agree on what they read."""

from __future__ import annotations

import datetime
import decimal
import json
from typing import TYPE_CHECKING, Any

import pyarrow as pa
import pytest
import semolina
from adbc_poolhouse import DatabricksPythonConfig, DuckDBConfig

from demo_stacks import bench, cli
from demo_stacks import databricks as dbx

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path
    from types import ModuleType

    from semolina.engines.base import Engine


@pytest.fixture
def models() -> ModuleType:
    return cli.load_models()


@pytest.fixture
def engine(warehouse: Path) -> Iterator[Engine]:
    with semolina.create_engine(
        DuckDBConfig(database=str(warehouse), read_only=True, pool_size=1), register=True
    ) as engine:
        yield engine


def test_run_times_every_query_down_every_path(engine: Engine, models: ModuleType) -> None:
    lines: list[str] = []
    results = bench.run(engine, models, repeats=2, warmup=1, progress=lines.append)
    assert [(r.query, r.path) for r in results] == [
        (q.name, p) for q in bench.QUERIES for p in bench.PATHS
    ]
    for r in results:
        assert len(r.runs) == 2
        assert all(ms > 0 for ms in r.runs)
    rows = {q.name: {r.rows for r in results if r.query == q.name} for q in bench.QUERIES}
    assert rows["region_year"] == {5 * 7}, "five regions by seven order years"
    assert all(len(n) == 1 and n.pop() > 0 for n in rows.values())
    assert lines[0] == "region_year: 35 rows"


def test_json_paths_time_their_stages(engine: Engine, models: ModuleType) -> None:
    results = bench.run(engine, models, queries=bench.QUERIES[:1], repeats=1, warmup=0)
    by_path = {r.path: r for r in results}
    for path in ("semolina_json", "semolina_json_validate"):
        assert set(by_path[path].stages) == {"into", "dump"}
    assert by_path["sql"].stages == by_path["semolina_arrow"].stages == {}


def test_dto_for_mirrors_the_schema() -> None:
    schema = pa.schema(
        [
            pa.field("n", pa.int64(), nullable=False),
            pa.field("x", pa.float64()),
            pa.field("d", pa.decimal128(38, 4)),
            pa.field("s", pa.string()),
            pa.field("day", pa.date32()),
            pa.field("at", pa.timestamp("us")),
            pa.field("ok", pa.bool_()),
        ]
    )
    fields = bench.dto_for(schema, "row").model_fields
    assert all(not f.is_required() for f in fields.values())
    assert fields["n"].annotation == int | None
    assert fields["x"].annotation == float | None
    assert fields["d"].annotation == decimal.Decimal | None
    assert fields["s"].annotation == str | None
    assert fields["day"].annotation == datetime.date | None
    assert fields["at"].annotation == datetime.datetime | None
    assert fields["ok"].annotation == bool | None


def test_dto_for_rejects_types_it_cannot_map() -> None:
    with pytest.raises(TypeError, match="no DTO type for Arrow list<item: int64>"):
        bench.dto_for(pa.schema([pa.field("xs", pa.list_(pa.int64()))]), "row")


def test_summary_and_table() -> None:
    r = bench.Result("q", "sql", rows=1234, runs=[3.0, 1.0, 2.0, 10.0])
    r.stages["into"] = [1.0, 3.0]
    s = r.summary()
    assert (s["median_ms"], s["p95_ms"], s["min_ms"]) == (2.5, 10.0, 1.0)
    assert s["stages_median_ms"] == {"into": 2.0}
    header, line = bench.table([r])
    assert header.split()[:3] == ["query", "path", "rows"]
    assert line.split()[:5] == ["q", "sql", "1,234", "2.5", "10.0"]
    assert line.endswith("into 2.0")


def test_cli_bench_on_a_duckdb_file(
    warehouse: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "results" / "bench.json"
    argv = ["bench", "--database", str(warehouse), "--query", "region_year", "--repeats", "1"]
    assert cli.main([*argv, "--out", str(out)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1 + len(bench.PATHS)
    summaries = json.loads(out.read_text())
    assert [s["path"] for s in summaries] == list(bench.PATHS)
    assert {s["rows"] for s in summaries} == {35}


def test_cli_bench_on_databricks_needs_catalog_and_scale_factor() -> None:
    with pytest.raises(SystemExit):
        cli.main(["bench", "--http-path", "/sql/1.0/warehouses/rt", "--sf", "1"])


def test_dbx_bench_engine_turns_the_result_cache_off(monkeypatch: pytest.MonkeyPatch) -> None:
    executed: list[str] = []
    configs: list[DatabricksPythonConfig] = []

    class Cursor:
        def execute(self, sql: str) -> None:
            executed.append(sql)

        def close(self) -> None:
            pass

    class Conn:
        def __enter__(self) -> Conn:
            return self

        def __exit__(self, *exc: object) -> None:
            pass

        def cursor(self) -> Cursor:
            return Cursor()

    class FakeEngine:
        def connect(self) -> Conn:
            return Conn()

    def create_engine(cfg: DatabricksPythonConfig, *, register: bool) -> Any:
        assert register is True
        configs.append(cfg)
        return FakeEngine()

    monkeypatch.setattr(semolina, "create_engine", create_engine)
    monkeypatch.setenv("DATABRICKS_HOST", "dbc-1.cloud.databricks.com")
    dbx.bench_engine("/sql/1.0/warehouses/rt", "cat", 1)
    ((cfg,),) = [configs]
    assert (cfg.catalog, cfg.schema_, cfg.pool_size, cfg.max_overflow) == (
        "cat",
        "tpch_sf1",
        1,
        0,
    )
    assert executed == ["SET use_cached_result = false"]
