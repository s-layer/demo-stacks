"""``CREATE SEMANTIC VIEW`` DDL for Snowflake."""

from __future__ import annotations

from typing import TYPE_CHECKING

from demo_stacks.render.duckdb import grammar_statements

if TYPE_CHECKING:
    from demo_stacks.semantic import SemanticView


def render(view: SemanticView, *, schema: str | None = None, replace: bool = True) -> str:
    """
    Snowflake DDL for ``view``.

    Args:
        view: The canonical view.
        schema: Prefix for every physical table, e.g.
            ``"SNOWFLAKE_SAMPLE_DATA.TPCH_SF10"``.
        replace: Emit ``CREATE OR REPLACE``.
    """
    return "".join(
        f"{stmt};\n"
        for stmt in grammar_statements(view, schema=schema, replace=replace, snowflake=True)
    )
