"""``demo-stacks`` command line: generate data, render view DDL, load a DuckDB target."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from demo_stacks import dataset, s3tables, semantic
from demo_stacks.render import databricks, duckdb, semolina, snowflake

GENERATED = semantic.REPO_ROOT / "generated"

# Where each warehouse's tables live in the committed renders. `demo-stacks ddl`
# renders for any other location.
DEFAULT_SCHEMAS = {
    "duckdb": s3tables.SCHEMA,
    "snowflake": "SNOWFLAKE_SAMPLE_DATA.TPCH_SF10",
    "databricks": "s_layer_demo.tpch",
}

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
    if args.check:
        for p in stale:
            print(f"out of date: {args.out / p}", file=sys.stderr)
        if stale:
            print("run `demo-stacks render` and commit the result", file=sys.stderr)
        return 1 if stale else 0
    for p in stale:
        (args.out / p).parent.mkdir(parents=True, exist_ok=True)
        (args.out / p).write_text(files[p])
        print(f"wrote {args.out / p}")
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

    defs = semantic.load(args.definitions)
    con = ddb.connect(str(args.database))
    try:
        if args.s3_tables_arn:
            for stmt in s3tables.setup_sql(args.s3_tables_arn, region=args.region):
                con.execute(stmt)
            schema = s3tables.SCHEMA
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
    p.add_argument("--views-only", action="store_true", help="skip loading, create views only")
    p.set_defaults(func=cmd_load)

    args = parser.parse_args(argv)
    if getattr(args, "func", None) is cmd_load and not args.views_only and not args.parquet:
        parser.error("load needs --parquet unless --views-only is given")
    return int(args.func(args))


def _read(path: Path) -> str | None:
    return path.read_text() if path.exists() else None


if __name__ == "__main__":
    raise SystemExit(main())
