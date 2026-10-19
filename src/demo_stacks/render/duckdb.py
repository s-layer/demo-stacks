"""
DDL for the duckdb-semantic-views extension.

The extension follows Snowflake's ``CREATE SEMANTIC VIEW`` grammar, so this module
also backs :mod:`demo_stacks.render.snowflake`; the differences are passed in as
flags rather than duplicated.
"""

from __future__ import annotations

from demo_stacks.render import qualify, sql_string
from demo_stacks.semantic import SemanticView, rewrite


def render(view: SemanticView, *, schema: str | None = None, replace: bool = True) -> str:
    """``CREATE SEMANTIC VIEW`` DDL for ``view`` as one script; see :func:`statements`."""
    return "".join(f"{stmt};\n" for stmt in statements(view, schema=schema, replace=replace))


def statements(view: SemanticView, *, schema: str | None = None, replace: bool = True) -> list[str]:
    """
    The DDL for ``view`` as separate statements, each to be executed on its own.

    duckdb-semantic-views rejects semantic-view DDL that shares a query string with
    other statements (its TECH-DEBT #85), so execute these one call at a time.

    Args:
        view: The canonical view.
        schema: Prefix for every physical table, e.g. ``"s3t.tpch"`` for an attached
            Iceberg catalog. ``None`` leaves tables unqualified.
        replace: Emit ``CREATE OR REPLACE`` so re-running the DDL is idempotent.
    """
    return grammar_statements(view, schema=schema, replace=replace, snowflake=False)


def grammar_statements(
    view: SemanticView, *, schema: str | None, replace: bool, snowflake: bool
) -> list[str]:
    owner = {m.name: m.table for m in view.metrics}

    def derived_expr(expr: str) -> str:
        if not snowflake:
            return expr
        # Snowflake resolves a derived metric's inputs as <table>.<metric>.
        return rewrite(expr, bare=lambda n: f"{owner[n]}.{n}" if owner.get(n) else n)

    def comment(text: str | None) -> str:
        return f" COMMENT = {sql_string(' '.join(text.split()))}" if text else ""

    tables = [
        f"{t.alias} AS {qualify(t.table, schema)} PRIMARY KEY ({', '.join(t.primary_key)})"
        f"{comment(t.description)}"
        for t in view.tables
    ]
    rels = [
        f"{r.name} AS {r.from_}({', '.join(r.from_columns)}) REFERENCES {r.to}"
        for r in view.relationships
    ]
    facts = [f"{f.table}.{f.name} AS {f.expr}{comment(f.description)}" for f in view.facts]
    dims = [f"{d.table}.{d.name} AS {d.expr}{comment(d.description)}" for d in view.dimensions]
    metrics = [
        (f"{m.table}.{m.name} AS {m.expr}" if m.table else f"{m.name} AS {derived_expr(m.expr)}")
        + comment(m.description)
        for m in view.metrics
    ]

    verb = "CREATE OR REPLACE SEMANTIC VIEW" if replace else "CREATE SEMANTIC VIEW"
    # duckdb-semantic-views needs AS before TABLES; Snowflake's grammar has no AS.
    head = f"{verb} {view.name}" if snowflake else f"{verb} {view.name} AS"
    parts = [head, _clause("TABLES", tables)]
    if rels:
        parts.append(_clause("RELATIONSHIPS", rels))
    if facts:
        parts.append(_clause("FACTS", facts))
    if dims:
        parts.append(_clause("DIMENSIONS", dims))
    if metrics:
        parts.append(_clause("METRICS", metrics))
    if view.description and snowflake:
        # Snowflake takes the view comment inline; duckdb-semantic-views sets it with ALTER.
        parts.append(f"COMMENT = {sql_string(' '.join(view.description.split()))}")
    stmts = ["\n".join(parts)]
    if view.description and not snowflake:
        stmts.append(
            f"ALTER SEMANTIC VIEW {view.name} SET COMMENT = "
            f"{sql_string(' '.join(view.description.split()))}"
        )
    return stmts


def _clause(keyword: str, items: list[str]) -> str:
    body = ",\n".join(f"    {item}" for item in items)
    return f"{keyword} (\n{body}\n)"
