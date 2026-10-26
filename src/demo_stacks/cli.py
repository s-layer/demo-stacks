"""``demo-stacks`` command line: generate data, render view DDL, load a DuckDB target."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from demo_stacks import bench, dataset, s3tables, semantic
from demo_stacks import databricks as dbx
from demo_stacks.render import databricks, duckdb, semolina, snowflake

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from types import ModuleType

    from semolina.engines.base import Engine

GENERATED = semantic.REPO_ROOT / "generated"
CHAT_UI = semantic.REPO_ROOT / "chat" / "dist"

# Where each warehouse's tables live in the committed renders. `demo-stacks ddl`
# renders for any other location.
DEFAULT_SCHEMAS = {
    "duckdb": s3tables.schema(10),
    "snowflake": "SNOWFLAKE_SAMPLE_DATA.TPCH_SF10",
    "databricks": dbx.schema("s_layer_demo", 10),
}

# demo_stacks.chat.DEFAULT_MODEL, repeated so `--help` works without the chat extra.
_CHAT_MODEL = "anthropic:claude-sonnet-5-5"

_SQL_RENDERERS: dict[str, Callable[..., str]] = {
    "duckdb": duckdb.render,
    "snowflake": snowflake.render,
    "databricks": databricks.render,
}


def rendered_files(defs: semantic.Definitions) -> dict[Path, str]:
    """Every file ``render`` writes, keyed by path relative to ``generated/``."""
    files: dict[Path, str] = {}
    for target, render in _SQL_RENDERERS.items():
        for view in defs.views:
            files[Path(target) / f"{view.name}.sql"] = render(view, schema=DEFAULT_SCHEMAS[target])
    files[Path("semolina") / f"{defs.dataset}_models.py"] = semolina.render(defs)
    return files


def cmd_render(args: argparse.Namespace) -> int:
    defs = semantic.load(args.definitions)
    files = rendered_files(defs)
    stale = [p for p, text in files.items() if _read(args.out / p) != text]
    # Files left behind by a renamed or removed view, in the directories render owns.
    owned = {p.parent for p in files}
    orphans = sorted(
        f.relative_to(args.out)
        for d in owned
        if (args.out / d).is_dir()
        for f in (args.out / d).iterdir()
        if f.is_file() and f.relative_to(args.out) not in files
    )
    if args.check:
        for p in stale:
            print(f"out of date: {args.out / p}", file=sys.stderr)
        for p in orphans:
            print(f"no longer rendered: {args.out / p}", file=sys.stderr)
        if stale or orphans:
            print("run `demo-stacks render` and commit the result", file=sys.stderr)
        return 1 if stale or orphans else 0
    for p in stale:
        (args.out / p).parent.mkdir(parents=True, exist_ok=True)
        (args.out / p).write_text(files[p])
        print(f"wrote {args.out / p}")
    for p in orphans:
        (args.out / p).unlink()
        print(f"removed {args.out / p}")
    return 0


def cmd_ddl(args: argparse.Namespace) -> int:
    defs = semantic.load(args.definitions)
    views = [defs.view(args.view)] if args.view else defs.views
    render = _SQL_RENDERERS[args.target]
    schema = args.schema if args.schema is not None else DEFAULT_SCHEMAS[args.target]
    print("\n".join(render(v, schema=schema or None) for v in views), end="")
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    counts = dataset.generate(args.scale_factor, args.out, chunks=args.chunks)
    for table, n in counts.items():
        print(f"{table:>10} {n:>14,}")
    return 0


def cmd_load(args: argparse.Namespace) -> int:
    import duckdb as ddb

    if args.s3_tables_arn and args.scale_factor is None:
        raise SystemExit("load --s3-tables-arn needs --scale-factor to pick a namespace")
    defs = semantic.load(args.definitions)
    con = ddb.connect(str(args.database))
    try:
        if args.s3_tables_arn:
            for stmt in s3tables.setup_sql(args.s3_tables_arn, region=args.region):
                con.execute(stmt)
            schema = s3tables.schema(args.scale_factor)
        else:
            schema = args.schema
        if not args.views_only:
            for table, n in dataset.load(con, args.parquet, schema=schema).items():
                print(f"loaded {schema}.{table}: {n:,} rows")
        con.execute("INSTALL semantic_views FROM community")
        con.execute("LOAD semantic_views")
        for view in defs.views:
            for stmt in duckdb.statements(view, schema=schema):
                con.execute(stmt)
            print(f"created semantic view {view.name}")
    finally:
        con.close()
    return 0


def cmd_dbx_load(args: argparse.Namespace) -> int:
    defs = semantic.load(args.definitions)
    with dbx.connect(args.http_path) as cursor:
        if not args.views_only:
            staging = f"{args.staging.rstrip('/')}/{dataset.schema_name(args.scale_factor)}"
            counts = dbx.load(cursor, staging, args.scale_factor, catalog=args.catalog)
            for table in dataset.TABLES:
                name = f"{dbx.schema(args.catalog, args.scale_factor)}.{table}"
                print(f"loaded {name}: {counts[table]:,} rows")
        for name in dbx.create_views(cursor, defs, args.scale_factor, catalog=args.catalog):
            print(f"created metric view {name}")
    return 0


def load_models(defs: semantic.Definitions | None = None) -> ModuleType:
    """Import the generated semolina models, ``generated/semolina/<dataset>_models.py``."""
    name = f"{(defs or semantic.load()).dataset}_models"
    spec = importlib.util.spec_from_file_location(name, GENERATED / "semolina" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cmd_dbx_query(args: argparse.Namespace) -> int:
    if args.semolina:
        query = dbx.semolina_sample(load_models(semantic.load(args.definitions)))
        with (
            dbx.semolina_engine(args.http_path, args.catalog, args.scale_factor),
            query.execute() as result,
        ):
            for row in result.fetchall_rows():
                print("\t".join(str(v) for v in row.values()))
        return 0
    with dbx.connect(args.http_path) as cursor:
        cursor.execute(args.sql or dbx.sample_query(args.catalog, args.scale_factor))
        for row in cursor.fetchall():
            print("\t".join(str(v) for v in row))
    return 0


def bench_engine(args: argparse.Namespace) -> Engine:
    """
    The registered engine ``bench`` times: a DuckDB file, or a Databricks warehouse.

    With ``--s3-tables-arn`` the DuckDB file holds stack 1's views, which read through
    the attached table bucket, so the engine attaches it before anything is timed.
    """
    import semolina

    if args.http_path:
        return dbx.bench_engine(args.http_path, args.catalog, args.scale_factor)
    from adbc_poolhouse import DuckDBConfig

    cfg = DuckDBConfig(database=str(args.database), read_only=True, pool_size=1)
    engine = semolina.create_engine(cfg, register=True)
    if args.s3_tables_arn:
        try:
            with engine.connect() as conn:
                cur = conn.cursor()
                try:
                    for stmt in s3tables.setup_sql(args.s3_tables_arn, region=args.region):
                        cur.execute(stmt)
                finally:
                    cur.close()
        except BaseException:
            engine.__exit__(None, None, None)
            raise
    return engine


def cmd_bench(args: argparse.Namespace) -> int:
    import json

    models = load_models(semantic.load(args.definitions))
    queries = tuple(q for q in bench.QUERIES if not args.query or q.name in args.query)
    with bench_engine(args) as engine:
        results = bench.run(
            engine,
            models,
            queries=queries,
            repeats=args.repeats,
            warmup=args.warmup,
            progress=lambda line: print(line, file=sys.stderr),
        )
    for line in bench.table(results):
        print(line)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps([r.summary() for r in results], indent=2) + "\n")
        print(f"wrote {args.out}", file=sys.stderr)
    return 0


def cmd_s3_bench(args: argparse.Namespace) -> int:
    from demo_stacks import remote

    return remote.s3_bench(
        args.scale_factor, repeats=args.repeats, instance_type=args.instance_type
    )


def cmd_chat(args: argparse.Namespace) -> int:
    from contextlib import AsyncExitStack

    import anyio
    import uvicorn

    from demo_stacks import chat

    static_dir = CHAT_UI if CHAT_UI.is_dir() else None
    if static_dir is None:
        print(f"{CHAT_UI} is not built; serving the API only (run `just chat-dev` for the UI)")

    async def serve() -> None:
        async with AsyncExitStack() as stack:
            validator = None
            if args.validate:
                from s_layer_flint_charts import FlintSidecar

                validator = await stack.enter_async_context(FlintSidecar())
            app = chat.create_app(
                args.database,
                definitions=semantic.load(args.definitions),
                model=args.model,
                validator=validator,
                static_dir=static_dir,
            )
            await uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port)).serve()

    anyio.run(serve)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="demo-stacks", description=__doc__)
    parser.add_argument(
        "--definitions", type=Path, default=semantic.DEFAULT_DEFINITIONS, help="canonical YAML"
    )
    sub = parser.add_subparsers(required=True)

    p = sub.add_parser("render", help="write generated/ from the canonical definitions")
    p.add_argument("--out", type=Path, default=GENERATED)
    p.add_argument("--check", action="store_true", help="fail if generated/ is out of date")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("ddl", help="print one target's DDL for a given table location")
    p.add_argument("target", choices=sorted(_SQL_RENDERERS))
    p.add_argument("--schema", help="table prefix, e.g. my_catalog.tpch ('' for none)")
    p.add_argument("--view", help="only this view")
    p.set_defaults(func=cmd_ddl)

    p = sub.add_parser("generate", help="generate TPC-H Parquet")
    p.add_argument("--scale-factor", "--sf", type=float, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--chunks", type=int, default=1)
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("load", help="load Parquet into DuckDB or S3 Tables and create the views")
    p.add_argument("--parquet", help="directory or s3:// prefix written by `generate`")
    p.add_argument("--database", default=":memory:", help="DuckDB file that holds the views")
    p.add_argument("--schema", default="tpch", help="schema for local tables")
    p.add_argument("--s3-tables-arn", help="load into this S3 Tables bucket instead")
    p.add_argument("--region", help="AWS region of the table bucket")
    p.add_argument(
        "--scale-factor",
        "--sf",
        type=float,
        help="with --s3-tables-arn: load into namespace tpch_sf<N>",
    )
    p.add_argument("--views-only", action="store_true", help="skip loading, create views only")
    p.set_defaults(func=cmd_load)

    p = sub.add_parser(
        "dbx-load",
        help="load staged Parquet into Unity Catalog and create the metric views (stack 2)",
    )
    p.add_argument("--scale-factor", "--sf", type=float, required=True)
    p.add_argument("--staging", help="staging_uri output of the data root; tpch_sf<N>/ below it")
    p.add_argument("--catalog", required=True)
    p.add_argument("--http-path", required=True, help="HTTP path of the loader warehouse")
    p.add_argument("--views-only", action="store_true", help="skip loading, create views only")
    p.set_defaults(func=cmd_dbx_load)

    p = sub.add_parser("dbx-query", help="run a query on a Databricks warehouse, e.g. the RT one")
    p.add_argument("--http-path", required=True)
    p.add_argument("--catalog", required=True)
    p.add_argument("--scale-factor", "--sf", type=float, required=True)
    how = p.add_mutually_exclusive_group()
    how.add_argument("--sql", help="query to run instead of the sample metric view query")
    how.add_argument(
        "--semolina", action="store_true", help="run the sample through the semolina models"
    )
    p.set_defaults(func=cmd_dbx_query)

    p = sub.add_parser(
        "bench", help="time direct SQL, semolina Arrow and semolina JSON on a stack's engine"
    )
    where = p.add_mutually_exclusive_group(required=True)
    where.add_argument("--database", type=Path, help="DuckDB file from `load`")
    where.add_argument("--http-path", help="Databricks warehouse, e.g. the RT one")
    p.add_argument(
        "--s3-tables-arn", help="with --database: attach this table bucket first (stack 1)"
    )
    p.add_argument("--region", help="with --s3-tables-arn: AWS region of the table bucket")
    p.add_argument("--catalog", help="with --http-path: the catalog holding tpch_sf<N>")
    p.add_argument("--scale-factor", "--sf", type=float, help="with --http-path: schema tpch_sf<N>")
    p.add_argument(
        "--query",
        action="append",
        choices=[q.name for q in bench.QUERIES],
        help="only this benchmark query (repeatable)",
    )
    p.add_argument("--repeats", type=int, default=10, help="timed runs per query and path")
    p.add_argument("--warmup", type=int, default=1, help="untimed runs first")
    p.add_argument("--out", type=Path, help="also write the summaries here as JSON")
    p.set_defaults(func=cmd_bench)

    p = sub.add_parser(
        "s3-bench",
        help="benchmark stack 1 on a runner: launch, follow its log, fetch results, clean up",
    )
    p.add_argument("--scale-factor", "--sf", type=float, required=True)
    p.add_argument("--repeats", type=int, default=10, help="timed runs per query and path")
    p.add_argument("--instance-type", default="r7i.2xlarge")
    p.set_defaults(func=cmd_s3_bench)

    p = sub.add_parser("chat", help="chat with an agent that charts the local DuckDB views")
    p.add_argument("--database", type=Path, required=True, help="DuckDB file from `load`")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument(
        "--model", help=f"Pydantic AI model name (default: $DEMO_CHAT_MODEL or {_CHAT_MODEL})"
    )
    p.add_argument(
        "--validate", action="store_true", help="also check each chart with Flint (needs npx)"
    )
    p.set_defaults(func=cmd_chat)

    args = parser.parse_args(argv)
    if (
        getattr(args, "func", None) is cmd_bench
        and args.http_path
        and (not args.catalog or args.scale_factor is None)
    ):
        parser.error("bench --http-path needs --catalog and --scale-factor")
    if getattr(args, "func", None) is cmd_bench and args.s3_tables_arn and args.http_path:
        parser.error("bench --s3-tables-arn goes with --database, not --http-path")
    if getattr(args, "func", None) is cmd_s3_bench and args.repeats < 1:
        parser.error("s3-bench needs --repeats of at least 1")
    if getattr(args, "func", None) is cmd_s3_bench and (
        args.scale_factor < 1 or not args.scale_factor.is_integer()
    ):
        # The runner's variables reject these, and then so would its destroy.
        parser.error("s3-bench needs a whole --scale-factor of at least 1")
    if getattr(args, "func", None) is cmd_bench and (args.repeats < 1 or args.warmup < 0):
        parser.error("bench needs --repeats of at least 1 and --warmup of at least 0")
    if getattr(args, "func", None) is cmd_load and not args.views_only and not args.parquet:
        parser.error("load needs --parquet unless --views-only is given")
    if getattr(args, "func", None) is cmd_dbx_load and not args.views_only and not args.staging:
        parser.error("dbx-load needs --staging unless --views-only is given")
    return int(args.func(args))


def _read(path: Path) -> str | None:
    return path.read_text() if path.exists() else None


if __name__ == "__main__":
    raise SystemExit(main())
