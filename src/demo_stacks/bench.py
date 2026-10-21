"""
Benchmark the ways an app can read a semantic view query, on any stack's engine.

Each query runs down four paths, timed separately:

- ``sql``: the SQL and parameters semolina would send, run on a raw ADBC cursor from the same pool,
  read with ``fetch_arrow_table()``. The floor: no semolina in the loop.
- ``semolina_arrow``: build the query with the generated models, ``execute()`` it and
  read with ``fetch_arrow_table()``. The gap to ``sql`` is semolina's own cost. Its
  ``execute`` and ``fetch`` stages are timed on their own.
- ``semolina_json``: the same query read into Pydantic DTOs with ``cursor.into()``
  (arrowmodel), then serialised with ``TypeAdapter.dump_json``, which is what a web
  endpoint would return. Its ``execute``, ``into`` and ``dump`` stages are timed on
  their own. ``into`` fetches the result and converts it, so ``into`` minus
  ``semolina_arrow``'s ``fetch`` is the conversion.
- ``semolina_json_validate``: the same with ``into(validate=True)``, arrowmodel's
  Pydantic-validated path, which a DTO whose types differ from the result's needs.

Every path runs on the engine's pool, so they share one driver and one set of
connections; only what happens around the query changes.
"""

from __future__ import annotations

import datetime
import decimal
import statistics
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from types import ModuleType

    import pyarrow as pa
    from pydantic import BaseModel, TypeAdapter
    from semolina.engines.base import Engine
    from semolina.query import Query


@dataclass(frozen=True)
class BenchQuery:
    """One query to time, built from the generated semolina models."""

    name: str
    description: str
    build: Callable[[ModuleType], Query]


def _region_year(models: ModuleType) -> Query:
    s = models.TpchSales
    return (
        s.query()
        .metrics(s.net_revenue, s.profit)
        .dimensions(s.customer_region, s.order_year)
        .order_by(s.customer_region, s.order_year)
    )


def _nation_month(models: ModuleType) -> Query:
    s = models.TpchSales
    return (
        s.query()
        .metrics(s.net_revenue, s.line_count, s.avg_discount)
        .dimensions(s.customer_nation, s.order_month)
        .order_by(s.customer_nation, s.order_month)
    )


def _brand_day(models: ModuleType) -> Query:
    s = models.TpchSales
    return (
        s.query()
        .metrics(s.net_revenue, s.quantity)
        .dimensions(s.part_brand, s.ship_date)
        .order_by(s.part_brand, s.ship_date)
    )


QUERIES = (
    BenchQuery("region_year", "revenue and profit by region and year, 35 rows", _region_year),
    BenchQuery("nation_month", "3 metrics by nation and month, about 2,000 rows", _nation_month),
    BenchQuery("brand_day", "2 metrics by brand and ship day, about 63,000 rows", _brand_day),
)
"""Small, medium and large results: where fixed and per-row costs each dominate."""

PATHS = ("sql", "semolina_arrow", "semolina_json", "semolina_json_validate")


@dataclass
class Result:
    """Timings for one query down one path, in milliseconds per run."""

    query: str
    path: str
    rows: int = 0
    runs: list[float] = field(default_factory=list[float])
    stages: dict[str, list[float]] = field(default_factory=dict[str, list[float]])

    def summary(self) -> dict[str, Any]:
        """Median, p95 and min per run, and the median of each stage."""
        return {
            "query": self.query,
            "path": self.path,
            "rows": self.rows,
            "runs": len(self.runs),
            "median_ms": statistics.median(self.runs),
            "p95_ms": _percentile(self.runs, 95),
            "min_ms": min(self.runs),
            "stages_median_ms": {k: statistics.median(v) for k, v in self.stages.items()},
        }


def _percentile(xs: list[float], p: float) -> float:
    ordered = sorted(xs)
    return ordered[min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1)))]


def _python_type(t: pa.DataType) -> type:
    import pyarrow as pa

    checks: list[tuple[Callable[[pa.DataType], bool], type]] = [
        (pa.types.is_boolean, bool),
        (pa.types.is_integer, int),
        (pa.types.is_floating, float),
        (pa.types.is_decimal, decimal.Decimal),
        (pa.types.is_string, str),
        (pa.types.is_large_string, str),
        (pa.types.is_timestamp, datetime.datetime),
        (pa.types.is_date, datetime.date),
    ]
    for check, py in checks:
        if check(t):
            return py
    raise TypeError(f"no DTO type for Arrow {t}")


def dto_for(schema: pa.Schema, name: str) -> type[BaseModel]:
    """
    A Pydantic model whose fields match ``schema``'s columns and types.

    Every field is optional, since a NULL can reach any column of an aggregate. This is
    the DTO an app would declare for the endpoint by hand (or with ``semolina
    codegen-dto``); building it from the schema keeps the queries editable.
    """
    from pydantic import create_model

    fields: dict[str, Any] = {
        n: (_python_type(t) | None, None) for n, t in zip(schema.names, schema.types, strict=True)
    }
    return create_model(name, **fields)


def _ms(start: int) -> float:
    return (time.perf_counter_ns() - start) / 1e6


def _run_sql(engine: Engine, sql: str, params: Any) -> tuple[int, dict[str, float]]:
    conn = engine.connect()
    try:
        cur = conn.cursor()
        try:
            cur.execute(sql, params)
            n = cur.fetch_arrow_table().num_rows
        finally:
            cur.close()
    finally:
        conn.close()
    return n, {}


def _run_semolina_arrow(query: Query) -> tuple[int, dict[str, float]]:
    t = time.perf_counter_ns()
    with query.execute() as cursor:
        execute = _ms(t)
        t = time.perf_counter_ns()
        n = cursor.fetch_arrow_table().num_rows
        fetch = _ms(t)
    return n, {"execute": execute, "fetch": fetch}


def _run_semolina_json(
    query: Query,
    dto: type[BaseModel],
    adapter: TypeAdapter[list[BaseModel]],
    *,
    validate: bool = False,
) -> tuple[int, dict[str, float]]:
    t = time.perf_counter_ns()
    with query.execute() as cursor:
        execute = _ms(t)
        t = time.perf_counter_ns()
        rows = cursor.into(dto, validate=validate)
        into = _ms(t)
    t = time.perf_counter_ns()
    body = adapter.dump_json(rows)
    dump = _ms(t)
    if not body.startswith(b"["):
        raise AssertionError("dump_json did not return a JSON array")
    return len(rows), {"execute": execute, "into": into, "dump": dump}


def _runners(
    engine: Engine, models: ModuleType, q: BenchQuery
) -> dict[str, Callable[[], tuple[int, dict[str, float]]]]:
    """One callable per path, each running ``q`` once and returning rows and stage times."""
    from pydantic import TypeAdapter

    query = q.build(models)
    # Exactly what Engine.execute sends: the same text and bound parameters.
    sql, params = engine.dialect.create_builder().build_select_with_params(query)
    with query.execute() as cursor:
        dto = dto_for(cursor.fetch_arrow_table().schema, f"{q.name}_row")
    # Built once, as an endpoint would at import. ``list[dto]`` is made at run time, so it
    # is a value here rather than a type expression.
    rows_type: Any = list[dto]
    adapter: TypeAdapter[list[BaseModel]] = TypeAdapter(rows_type)
    return {
        "sql": lambda: _run_sql(engine, sql, params),
        "semolina_arrow": lambda: _run_semolina_arrow(q.build(models)),
        "semolina_json": lambda: _run_semolina_json(q.build(models), dto, adapter),
        "semolina_json_validate": lambda: _run_semolina_json(
            q.build(models), dto, adapter, validate=True
        ),
    }


def run(
    engine: Engine,
    models: ModuleType,
    *,
    queries: tuple[BenchQuery, ...] = QUERIES,
    repeats: int = 10,
    warmup: int = 1,
    progress: Callable[[str], None] | None = None,
) -> list[Result]:
    """
    Time every query down every path on ``engine``, which must be registered.

    Paths are interleaved within each repeat, so a warehouse that speeds up or slows
    down over the run affects them alike. Every run, warmups included, must return the
    same number of rows, so a short or empty read fails the benchmark instead of
    timing it.

    Raises:
        ValueError: If ``repeats`` is below 1 or ``warmup`` below 0.
        AssertionError: If any run's row count differs from the query's first.
    """
    if repeats < 1 or warmup < 0:
        raise ValueError(f"need repeats >= 1 and warmup >= 0, got {repeats} and {warmup}")
    results: list[Result] = []
    for q in queries:
        runners = _runners(engine, models, q)
        by_path = {p: Result(q.name, p) for p in PATHS}
        expected: int | None = None
        for i in range(warmup + repeats):
            for path, fn in runners.items():
                t = time.perf_counter_ns()
                n, stages = fn()
                elapsed = _ms(t)
                if expected is None:
                    expected = n
                elif n != expected:
                    raise AssertionError(
                        f"{q.name}: {path} run {i} read {n:,} rows, expected {expected:,}"
                    )
                if i < warmup:
                    continue
                r = by_path[path]
                r.rows = n
                r.runs.append(elapsed)
                for k, v in stages.items():
                    r.stages.setdefault(k, []).append(v)
        results += by_path.values()
        if progress:
            progress(f"{q.name}: {expected:,} rows")
    return results


def table(results: list[Result]) -> Iterator[str]:
    """A plain-text table of the results, one line per query and path."""
    yield f"{'query':<14}{'path':<24}{'rows':>8}{'median ms':>11}{'p95 ms':>9}  stages (median ms)"
    for r in results:
        s = r.summary()
        stages = ", ".join(f"{k} {v:.1f}" for k, v in s["stages_median_ms"].items())
        yield (
            f"{r.query:<14}{r.path:<24}{r.rows:>8,}{s['median_ms']:>11.1f}{s['p95_ms']:>9.1f}"
            f"  {stages}"
        )
